"""The JWT API's transfer endpoints (`/api/ft/transfers/`, spec section 14): create a draft, list
and get transfers, finalize (send) a draft, update or delete an existing one, and manage its
recipients. Mirrors the web dashboard/send flow (`apps.file_transfer.views`) exactly -- every
endpoint here calls the same `apps.file_transfer.services` functions those views do, so the same
validation, limits and credit rules apply either way (CLAUDE.md's HTMX-views convention extends to
this API too, even though it isn't itself an HTMX form view).

Only logged-in users get here (`auth=JWTAuth()`): anonymous sending is web-only (spec section 14).
"""

from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.billing.services import InsufficientCreditsError
from apps.core import ratelimit

from .. import services
from ..models import ENDED_STATUSES, Transfer, TransferRecipient, TransferStatus
from ..schemas import (
    AddRecipientsSchema,
    FinalizeTransferSchema,
    TransferListSchema,
    TransferSchema,
    TransferUpdateSchema,
)
from .router import router

auth = JWTAuth()

#: Never returned by the list/get/update endpoints: a draft is an in-progress upload the sender
#: hasn't finalized yet, not a transfer in the spec section 6 sense -- same exclusion the
#: dashboard applies (`views.dashboard._UNLISTED_STATUSES`). Reached only through the file
#: endpoints (`api/files.py`) and `POST /transfers/{id}/send`.
_UNLISTED_STATUSES = (TransferStatus.DRAFT, TransferStatus.PENDING_CONFIRMATION)

ACTIVE_FILTER = 'active'
ENDED_FILTER = 'ended'
ALL_FILTER = 'all'
_VALID_FILTERS = (ACTIVE_FILTER, ENDED_FILTER, ALL_FILTER)


def _owned_transfer(request, transfer_id: UUID, *, include_drafts: bool = False) -> Transfer:
    qs = Transfer.objects.filter(owner=request.auth)
    if not include_drafts:
        qs = qs.exclude(status__in=_UNLISTED_STATUSES)
    return get_object_or_404(
        qs.prefetch_related('recipients', 'files'),
        id=transfer_id,
    )


@router.post('/transfers/', response={201: TransferSchema}, auth=auth)
def create_transfer(request):
    """Start a new transfer: an empty draft, exactly like visiting the web send page -- files are
    then added one at a time through the endpoints in `api/files.py`, and
    `POST /transfers/{id}/send` applies the recipients/expiry/etc. options and finalizes it,
    mirroring the web flow's own two-step shape (upload first, options at send time).

    Reuses the caller's existing empty draft rather than creating a fresh one on every call
    (`services.get_or_create_draft`, same as `views.send.send_page`) -- unlike the web page, this
    is a plain API call a script could retry or call in a loop, and without this, each call left
    another "Untitled transfer" draft row behind for the 24h `cleanup_drafts` grace period.
    Rate-limited on top of that for the same reason every other upload-side endpoint here is.
    """
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'FT_UPLOAD_USER'))
    transfer = services.get_or_create_draft(request.auth)
    return 201, transfer


@router.get('/transfers/', response=TransferListSchema, auth=auth)
def list_transfers(
    request,
    filter: str = ACTIVE_FILTER,  # noqa: A002 -- matches the web dashboard's own `?filter=`
    limit: int = 20,
    offset: int = 0,
):
    """List the caller's transfers (spec section 14: "list transfers (filter active/ended,
    pagination)"), same active/ended split as the dashboard (`views.dashboard._transfers_for`).

    `limit`/`offset` are plain query params (not `ninja.Query(..., ge=..., le=...)`: under
    `TYPE_CHECKING` that helper is a subscriptable type alias rather than a callable, which `ty`
    -- unlike mypy, which special-cases it away -- takes at face value and flags as
    `call-non-callable`) clamped by hand instead.
    """
    if filter not in _VALID_FILTERS:
        raise HttpError(400, f'filter must be one of: {", ".join(_VALID_FILTERS)}.')
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    qs = (
        Transfer.objects.filter(owner=request.auth)
        .exclude(status__in=_UNLISTED_STATUSES)
        .annotate(download_events_count=Count('download_events', distinct=True))
        .prefetch_related('recipients', 'files')
    )
    if filter == ENDED_FILTER:
        qs = qs.filter(status__in=ENDED_STATUSES)
    elif filter != ALL_FILTER:
        qs = qs.exclude(status__in=ENDED_STATUSES)

    count = qs.count()
    results = list(qs.order_by('-created_at')[offset : offset + limit])
    return {'count': count, 'limit': limit, 'offset': offset, 'results': results}


@router.get('/transfers/{transfer_id}', response=TransferSchema, auth=auth)
def get_transfer(request, transfer_id: UUID):
    """Get one transfer, including a still-in-progress draft -- unlike the list endpoint (which
    never shows a draft, same as the dashboard), a caller that already knows a specific id
    (typically its own, from `POST /transfers/`) needs to be able to look it up to resume an
    interrupted upload (`admin/filetransfer.py send --draft-id`)."""
    return _owned_transfer(request, transfer_id, include_drafts=True)


@router.post('/transfers/{transfer_id}/send', response=TransferSchema, auth=auth)
def finalize_transfer(request, transfer_id: UUID, payload: FinalizeTransferSchema):
    """Apply the sender's options and send the transfer (spec section 2 and 14) -- the JWT API's
    equivalent of the web send page's options form submit (`views.send.send_submit`), calling the
    exact same service so the same rules (recipients, expiry, the 1-credit minimum, ...) apply."""
    limited = ratelimit.hit_user(request.auth, 'FT_UPLOAD_USER')
    ratelimit.enforce(limited)
    transfer = _owned_transfer(request, transfer_id, include_drafts=True)
    if transfer.status != TransferStatus.DRAFT:
        raise HttpError(400, 'Transfer has already been sent.')

    options = services.SendOptions(
        recipients=payload.recipients,
        message=payload.message,
        expiry_choice=payload.expiry_choice,
        expiry_date=payload.expiry_date,
        max_downloads=payload.max_downloads,
        password=payload.password,
        notify_on_download=payload.notify_on_download,
    )
    try:
        services.finalize_send(transfer, options)
    except ValidationError as exc:
        raise HttpError(400, exc.messages[0])
    except InsufficientCreditsError as exc:
        # 402 Payment Required: the transfer itself is otherwise valid, but the account can't
        # cover it right now (see CLAUDE.md's decision note on this vs. 400 for the same case).
        raise HttpError(402, str(exc))
    return transfer


@router.patch('/transfers/{transfer_id}', response=TransferSchema, auth=auth)
def update_transfer(request, transfer_id: UUID, payload: TransferUpdateSchema):
    """Update a sent transfer (spec section 14: "update (disable, re-enable, expiry, max
    downloads, password, notify toggle)") -- every field is optional, and only the ones actually
    present in the request body are applied (see `TransferUpdateSchema`'s docstring), one dashboard
    action's service call per field, exactly as the dashboard's own combined settings form does
    (`views.dashboard.update_settings`)."""
    transfer = _owned_transfer(request, transfer_id)
    data = payload.dict(exclude_unset=True)
    if 'expiry_date' in data and 'expiry_choice' not in data:
        raise HttpError(400, 'expiry_date requires expiry_choice.')

    try:
        # Atomic: several of these are separate `UPDATE`s (one service call per field, mirroring
        # `views.dashboard.update_settings`'s own combined form), so a later field failing (e.g.
        # `max_downloads` after `expiry_choice` already applied) must not leave the earlier ones
        # committed -- a partial update from one PATCH call is worse than rejecting it outright.
        with transaction.atomic():
            if 'disabled' in data:
                if data['disabled']:
                    services.disable_transfer(transfer)
                else:
                    services.reenable_transfer_action(transfer)
            if 'expiry_choice' in data:
                services.set_expiry(transfer, data['expiry_choice'], data.get('expiry_date'))
            if 'max_downloads' in data:
                services.set_max_downloads(transfer, data['max_downloads'])
            if data.get('remove_password'):
                services.remove_password(transfer)
            elif data.get('password'):
                services.set_password(transfer, data['password'])
            if 'notify_on_download' in data:
                services.set_notify_on_download(transfer, data['notify_on_download'])
    except ValidationError as exc:
        raise HttpError(400, exc.messages[0])
    except InsufficientCreditsError as exc:
        raise HttpError(402, str(exc))

    transfer.refresh_from_db()
    return transfer


@router.delete('/transfers/{transfer_id}', response={204: None}, auth=auth)
def delete_transfer(request, transfer_id: UUID):
    """Delete a transfer now (spec section 14). A still-in-progress draft is simply abandoned
    (`services.abort_draft`, the same cleanup `cleanup_drafts` would eventually do) rather than
    routed through `services.delete_transfer_now`, which only accepts a transfer that was actually
    sent (`Transfer.is_actionable`)."""
    transfer = _owned_transfer(request, transfer_id, include_drafts=True)
    if transfer.status == TransferStatus.DRAFT:
        services.abort_draft(transfer)
    else:
        try:
            services.delete_transfer_now(transfer)
        except ValidationError as exc:
            raise HttpError(400, exc.messages[0])
    return 204, None


@router.post('/transfers/{transfer_id}/recipients', response=TransferSchema, auth=auth)
def add_recipients(request, transfer_id: UUID, payload: AddRecipientsSchema):
    transfer = _owned_transfer(request, transfer_id)
    try:
        services.add_recipients(transfer, payload.recipients)
    except ValidationError as exc:
        raise HttpError(400, exc.messages[0])
    transfer.refresh_from_db()
    return transfer


@router.post(
    '/transfers/{transfer_id}/recipients/{recipient_id}/resend',
    response=TransferSchema,
    auth=auth,
)
def resend_recipient(request, transfer_id: UUID, recipient_id: UUID):
    transfer = _owned_transfer(request, transfer_id)
    recipient = get_object_or_404(TransferRecipient, id=recipient_id, transfer=transfer)
    try:
        services.resend_recipient_email(recipient)
    except ValidationError as exc:
        raise HttpError(400, exc.messages[0])
    return transfer
