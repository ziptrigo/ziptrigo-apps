"""The owner's side of file requests (issue #306): the page that creates one and its "created"
page, and the dashboard row's actions (mark as done, reopen, settings, remove a file, resend an
invite). Disable, re-enable and delete now are the transfer actions in `views.dashboard`, which
answer a request's row too.

The create form is an htmx form view (422 + the errors partial on a refusal, `hx_redirect` on
success); the actions answer the re-rendered row like every dashboard action. All of it calls
`services.requests`, which the JWT API calls too.
"""

from functools import partial

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError, get_balance
from apps.core import ratelimit
from apps.core.htmx import hx_redirect
from apps.core.services.client_ip import client_ip

from .. import services
from ..forms import FileRequestForm, RequestSettingsForm, add_service_errors
from ..models import FileTransferSettings, RequestInvite, Transfer, TransferFile, TransferKind
from ..services.actions import TransferHeldError
from ..services.blocklist import BlockedSenderError, is_blocked
from .dashboard import _form_error_text, _recipient_email_limited, _respond


def _errors(request: AuthenticatedHttpRequest, form: FileRequestForm, status: int = 422):
    response = render(
        request, 'file_transfer/partials/send_errors.html', {'form': form}, status=status
    )
    response['HX-Retarget'] = '#send-errors'
    response['HX-Reswap'] = 'innerHTML'
    return response


def _blocked(request: AuthenticatedHttpRequest) -> HttpResponse:
    return render(request, 'file_transfer/blocked.html', status=403)


@login_required
@require_http_methods(['GET', 'POST'])
def request_new(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The "Request files" page and its submit."""
    ip = client_ip(request)
    if is_blocked(email=request.user.email, ip=ip):
        # Block list (issue #59): checked before the page even renders, like the send page.
        if request.method == 'POST':
            return hx_redirect(request, reverse('file_transfer:request-new'))
        return _blocked(request)

    settings_row = FileTransferSettings.load()
    if request.method == 'GET':
        context = {
            'form': FileRequestForm(),
            'settings': settings_row,
            'balance': get_balance(request.user),
            'min_balance_to_send': services.MIN_BALANCE_TO_SEND,
        }
        return render(request, 'file_transfer/request_new.html', context)

    form = FileRequestForm(request.POST)
    if not form.is_valid():
        return _errors(request, form)

    try:
        transfer = services.requests.create_request(
            request.user,
            form.to_options(),
            ip=ip,
            charge=partial(services.requests.charge_request_creation, request.user),
        )
    except BlockedSenderError:
        return hx_redirect(request, reverse('file_transfer:request-new'))
    except ratelimit.RateLimitExceeded as exc:
        form.add_error(None, exc.message)
        response = _errors(request, form, status=429)
        response['Retry-After'] = str(exc.retry_after)
        return response
    except InsufficientCreditsError as exc:
        form.add_error(None, str(exc))
        return _errors(request, form)
    except ValidationError as exc:
        add_service_errors(form, exc)
        return _errors(request, form)

    return hx_redirect(request, reverse('file_transfer:request-created', args=[transfer.id]))


@login_required
@require_GET
def request_created(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    """The page shown right after creating a request: the link, Copy, the QR code."""
    transfer = get_object_or_404(
        Transfer, id=transfer_id, owner=request.user, kind=TransferKind.REQUEST
    )
    context = {
        'transfer': transfer,
        'download_url': transfer.absolute_download_url,
        'invite_count': transfer.invites.count(),
    }
    return render(request, 'file_transfer/request_created.html', context)


def _owned_request(request: AuthenticatedHttpRequest, transfer_id: str) -> Transfer:
    return get_object_or_404(
        Transfer, id=transfer_id, owner=request.user, kind=TransferKind.REQUEST
    )


@login_required
@require_POST
def request_mark_done(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    """The owner's "Mark as done" (allowed with no files; sends no notice)."""
    transfer = _owned_request(request, transfer_id)
    try:
        services.requests.owner_mark_done(transfer, request.user)
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)
    transfer.refresh_from_db()
    return _respond(request, transfer)


@login_required
@require_POST
def request_reopen(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_request(request, transfer_id)
    try:
        services.requests.reopen(transfer)
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)
    return _respond(request, transfer)


@login_required
@require_POST
def request_update_settings(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_request(request, transfer_id)
    form = RequestSettingsForm(request.POST)
    if not form.is_valid():
        return _respond(request, transfer, error=_form_error_text(form), status=422)
    try:
        services.requests.update_request(
            transfer,
            form.to_changes(),
            ip=client_ip(request),
            charge=partial(services.charge_recipient_emails, request.user),
        )
    except ratelimit.RateLimitExceeded as exc:
        return _recipient_email_limited(request, transfer, exc)
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)
    return _respond(request, transfer)


@login_required
@require_POST
def request_remove_file(
    request: AuthenticatedHttpRequest, transfer_id: str, file_id: str
) -> HttpResponse:
    """The owner removes any file of their request (open or done)."""
    transfer = _owned_request(request, transfer_id)
    file = get_object_or_404(TransferFile, id=file_id, transfer=transfer)
    try:
        services.request_upload.remove_file_as_owner(transfer, file)
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)
    transfer.refresh_from_db()
    return _respond(request, transfer, notice='File removed.')


@login_required
@require_POST
def request_resend_invite(
    request: AuthenticatedHttpRequest, transfer_id: str, invite_id: str
) -> HttpResponse:
    transfer = _owned_request(request, transfer_id)
    invite = get_object_or_404(RequestInvite, id=invite_id, transfer=transfer)
    try:
        services.request_invites.resend_invite(
            invite,
            ip=client_ip(request),
            charge=partial(services.charge_recipient_emails, request.user),
        )
    except ratelimit.RateLimitExceeded as exc:
        return _recipient_email_limited(request, transfer, exc)
    except (TransferHeldError, ValidationError) as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)
    return _respond(request, transfer, notice=f'Sent the email to {invite.email} again.')
