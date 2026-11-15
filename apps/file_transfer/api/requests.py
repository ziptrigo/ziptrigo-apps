"""The JWT API's file request endpoints (`/api/ft/requests`, issue #306): the owner's side of a
request -- create it, list and read it with its files and invitees, change it, mark it done or
reopen it, disable and re-enable it, delete it now, resend an invite, download or remove a file.

Every endpoint calls the same `apps.file_transfer.services` the web dashboard does, so the same
rules apply. **Uploading through a request stays web only** (it is for the people the owner asks,
and they upload in a browser). `GET /transfers` lists sends only; a request is never a transfer
here, and another user's request is a 404.

Status codes, as the rest of `/api/ft/`: 400 for a refused field (`field: message`), 402 for too
few credits, 403 for a blocked owner, 404 for another user's request or file (never 403), 409 for a
request held for abuse review, 429 from the site-wide rate-limit handler.
"""

from collections.abc import Generator
from contextlib import contextmanager
from functools import partial
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.billing.services import InsufficientCreditsError
from apps.core.services.client_ip import client_ip

from .. import services
from ..models import (
    ENDED_STATUSES,
    DownloadEvent,
    RequestInvite,
    Transfer,
    TransferFile,
    TransferKind,
)
from ..schemas import (
    FileRequestCreateSchema,
    FileRequestFileUrlSchema,
    FileRequestListSchema,
    FileRequestSchema,
    FileRequestUpdateSchema,
)
from ..services.actions import TransferHeldError
from ..services.blocklist import BlockedSenderError
from ..services.storage import GET_URL_EXPIRES_SECONDS, get_storage
from .router import router

auth = JWTAuth()

ACTIVE_FILTER = 'active'
ENDED_FILTER = 'ended'
ALL_FILTER = 'all'
_VALID_FILTERS = (ACTIVE_FILTER, ENDED_FILTER, ALL_FILTER)

#: Keys of `PATCH /requests/{id}` where an explicit `null` is not "clear it" but a mistake.
_NOT_NULLABLE = ('title', 'message', 'upload_audience', 'emails', 'expiry_choice')


def _error_text(exc: ValidationError) -> str:
    """`field: message` for an error keyed by a field, the plain message otherwise."""
    if hasattr(exc, 'error_dict'):
        field, errors = next(iter(exc.error_dict.items()))
        return f'{field}: {errors[0].messages[0]}'
    return exc.messages[0]


@contextmanager
def _mapped_errors() -> Generator[None]:
    """The service's refusals as the API's status codes."""
    try:
        yield
    except TransferHeldError as exc:
        raise HttpError(409, exc.messages[0])
    except BlockedSenderError as exc:
        raise HttpError(403, exc.messages[0])
    except ValidationError as exc:
        raise HttpError(400, _error_text(exc))
    except InsufficientCreditsError as exc:
        raise HttpError(402, str(exc))


def _requests_for(user):
    return (
        Transfer.objects.filter(owner=user, kind=TransferKind.REQUEST)
        .select_related('done_by_user')
        .prefetch_related('files', 'invites')
    )


def _owned_request(request, request_id: UUID) -> Transfer:
    return get_object_or_404(_requests_for(request.auth), id=request_id)


def _fresh(request, request_id: UUID) -> Transfer:
    """The request again after a write, with the prefetches the schema reads."""
    return _owned_request(request, request_id)


@router.post('/requests', response={201: FileRequestSchema}, auth=auth)
def create_request(request, payload: FileRequestCreateSchema):
    """Create a file request: a link for other people to upload files to you. Free to create (the
    balance only needs the 1-credit minimum a send needs); what is uploaded is billed to you per
    day like a transfer's storage. Uploading is for the people you share the link with, in the
    browser."""
    options = services.requests.RequestOptions(
        title=payload.title,
        message=payload.message,
        upload_audience=payload.upload_audience,
        emails=payload.emails,
        max_files=payload.max_files,
        max_total_size_bytes=payload.max_total_size_bytes,
        expiry_choice=payload.expiry_choice,
        expiry_date=payload.expiry_date,
        password=payload.password,
    )
    with _mapped_errors():
        transfer = services.requests.create_request(
            request.auth,
            options,
            ip=client_ip(request),
            charge=partial(services.requests.charge_request_creation, request.auth),
        )
    return 201, _fresh(request, UUID(str(transfer.pk)))


@router.get('/requests', response=FileRequestListSchema, auth=auth)
def list_requests(
    request,
    filter: str = ACTIVE_FILTER,  # noqa: A002 -- matches the dashboard's own `?filter=`
    limit: int = 20,
    offset: int = 0,
):
    """List the caller's file requests, active or ended (or all), newest first."""
    if filter not in _VALID_FILTERS:
        raise HttpError(400, f'filter must be one of: {", ".join(_VALID_FILTERS)}.')
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    qs = _requests_for(request.auth)
    if filter == ENDED_FILTER:
        qs = qs.filter(status__in=ENDED_STATUSES)
    elif filter != ALL_FILTER:
        qs = qs.exclude(status__in=ENDED_STATUSES)
    count = qs.count()
    results = list(qs.order_by('-created_at')[offset : offset + limit])
    return {'count': count, 'limit': limit, 'offset': offset, 'results': results}


@router.get('/requests/{request_id}', response=FileRequestSchema, auth=auth)
def get_request(request, request_id: UUID):
    """One request with the files uploaded so far (who uploaded each, never an IP), its invitees
    and whether it is open or done."""
    return _owned_request(request, request_id)


@router.patch('/requests/{request_id}', response=FileRequestSchema, auth=auth)
def update_request(request, request_id: UUID, payload: FileRequestUpdateSchema):
    """Change a request: title, message, audience and invitees, limits (never below what is
    already uploaded), expiry and password -- only the fields sent, all or nothing."""
    transfer = _owned_request(request, request_id)
    data = payload.dict(exclude_unset=True)
    if 'expiry_date' in data and 'expiry_choice' not in data:
        raise HttpError(400, 'expiry_date requires expiry_choice.')
    for key in _NOT_NULLABLE:
        if key in data and data[key] is None:
            raise HttpError(400, f'{key} cannot be null.')

    with _mapped_errors(), transaction.atomic():
        services.requests.update_request(
            transfer,
            data,
            ip=client_ip(request),
            charge=partial(services.charge_recipient_emails, request.auth),
        )
    return _fresh(request, request_id)


@router.delete('/requests/{request_id}', response={204: None}, auth=auth)
def delete_request(request, request_id: UUID):
    """Delete a request now: its files are removed immediately."""
    transfer = _owned_request(request, request_id)
    with _mapped_errors():
        services.delete_transfer_now(transfer)
    return 204, None


@router.post('/requests/{request_id}/done', response=FileRequestSchema, auth=auth)
def mark_request_done(request, request_id: UUID):
    """Mark the request done (allowed with no files; nobody is notified): no more uploads, and
    the link shows the download view. Done is not an error when it already is."""
    transfer = _owned_request(request, request_id)
    with _mapped_errors():
        services.requests.owner_mark_done(transfer, request.auth)
    return _fresh(request, request_id)


@router.post('/requests/{request_id}/reopen', response=FileRequestSchema, auth=auth)
def reopen_request(request, request_id: UUID):
    """Reopen a done request: the link accepts uploads again and the files stay."""
    transfer = _owned_request(request, request_id)
    with _mapped_errors():
        services.requests.reopen(transfer)
    return _fresh(request, request_id)


@router.post('/requests/{request_id}/disable', response=FileRequestSchema, auth=auth)
def disable_request(request, request_id: UUID):
    transfer = _owned_request(request, request_id)
    with _mapped_errors():
        services.disable_transfer(transfer)
    return _fresh(request, request_id)


@router.post('/requests/{request_id}/reenable', response=FileRequestSchema, auth=auth)
def reenable_request(request, request_id: UUID):
    transfer = _owned_request(request, request_id)
    with _mapped_errors():
        services.reenable_transfer_action(transfer, ip=client_ip(request))
    return _fresh(request, request_id)


@router.post(
    '/requests/{request_id}/invites/{invite_id}/resend', response=FileRequestSchema, auth=auth
)
def resend_invite(request, request_id: UUID, invite_id: UUID):
    """Email one invitee again (a 10 minute cooldown per person; spends the same per-user email
    budget as resending a transfer's recipient)."""
    transfer = _owned_request(request, request_id)
    invite = get_object_or_404(RequestInvite, id=invite_id, transfer=transfer)
    with _mapped_errors():
        services.request_invites.resend_invite(
            invite,
            ip=client_ip(request),
            charge=partial(services.charge_recipient_emails, request.auth),
        )
    return _fresh(request, request_id)


@router.get(
    '/requests/{request_id}/files/{file_id}/url', response=FileRequestFileUrlSchema, auth=auth
)
def file_url(request, request_id: UUID, file_id: UUID):
    """A short-lived presigned URL to download one uploaded file (counted in the request's
    download log). Not available while the request is on hold or has ended."""
    transfer = _owned_request(request, request_id)
    file = get_object_or_404(TransferFile, id=file_id, transfer=transfer, uploaded=True)
    with _mapped_errors():
        if not transfer.is_actionable:
            raise ValidationError('This request has already ended.')
        if transfer.held_for_review_at:
            raise TransferHeldError('This request is on hold pending review.')
    DownloadEvent.objects.create(transfer=transfer, file=file, ip=client_ip(request))
    url = get_storage().presigned_get_url(file.storage_key, file.name)
    return {'url': url, 'filename': file.name, 'expires_in': GET_URL_EXPIRES_SECONDS}


@router.delete('/requests/{request_id}/files/{file_id}', response={204: None}, auth=auth)
def remove_request_file(request, request_id: UUID, file_id: UUID):
    """Remove one file from a request, open or done (it is deleted for good)."""
    transfer = _owned_request(request, request_id)
    file = get_object_or_404(TransferFile, id=file_id, transfer=transfer)
    with _mapped_errors():
        services.request_upload.remove_file_as_owner(transfer, file)
    return 204, None
