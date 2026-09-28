"""The logged-in dashboard (spec section 6): list with an active/ended filter, and per-transfer
actions. Actions re-render the transfer's row (htmx conventions from `CLAUDE.md`: 422 + the same
partial on validation errors, the updated partial on success). Errors -- whether a form's or a
service's `ValidationError`/`InsufficientCreditsError` -- are surfaced as one plain message in the
row rather than per-field, since the row's inputs reflect the transfer's current (still valid)
state rather than the just-submitted one.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.forms import Form
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError
from apps.core.htmx import is_htmx

from .. import services
from ..forms import AddRecipientsForm, TransferSettingsActionForm
from ..models import ENDED_STATUSES, Transfer, TransferRecipient, TransferStatus

ACTIVE_FILTER = 'active'
ENDED_FILTER = 'ended'

#: Never shown on the dashboard: a draft (or, in phase 2, unconfirmed) transfer isn't a "sent"
#: transfer yet, and `send_page` creates one on every visit, so without this exclusion every
#: reload of the send page would add an "Untitled transfer" row to the active list for up to the
#: 24h `cleanup_drafts` grace period.
_UNLISTED_STATUSES = (TransferStatus.DRAFT, TransferStatus.PENDING_CONFIRMATION)


def _transfers_for(user, filter_value: str):
    qs = (
        Transfer.objects.filter(owner=user)
        .exclude(status__in=_UNLISTED_STATUSES)
        # Avoids a `COUNT` query per row for `Transfer.download_count` (used by both
        # `download_count` and `downloads_remaining`); `files`/`recipients` are prefetched
        # instead of annotated since the row template needs the objects themselves, not just a
        # count, and `display_name` relies on `files` being prefetched too (see its docstring).
        .annotate(download_events_count=Count('download_events', distinct=True))
        .prefetch_related('files', 'recipients')
        .order_by('-created_at')
    )
    if filter_value == ENDED_FILTER:
        return qs.filter(status__in=ENDED_STATUSES)
    return qs.exclude(status__in=ENDED_STATUSES)


def _form_error_text(form: Form) -> str:
    return ' '.join(message for field_errors in form.errors.values() for message in field_errors)


@login_required
@require_GET
def dashboard(request: AuthenticatedHttpRequest) -> HttpResponse:
    filter_value = request.GET.get('filter', ACTIVE_FILTER)
    context = {'transfers': _transfers_for(request.user, filter_value), 'filter': filter_value}
    return render(request, 'file_transfer/dashboard.html', context)


@login_required
@require_GET
def transfer_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """HTMX partial: the transfer list, for switching the active/ended filter without a reload."""
    filter_value = request.GET.get('filter', ACTIVE_FILTER)
    context = {'transfers': _transfers_for(request.user, filter_value), 'filter': filter_value}
    return render(request, 'file_transfer/partials/transfer_list.html', context)


def _owned_transfer(request: AuthenticatedHttpRequest, transfer_id: str) -> Transfer:
    return get_object_or_404(Transfer, id=transfer_id, owner=request.user)


def _row_response(
    request: AuthenticatedHttpRequest, transfer: Transfer, *, error: str = '', status: int = 200
) -> HttpResponse:
    return render(
        request,
        'file_transfer/partials/transfer_row.html',
        {'transfer': transfer, 'error': error},
        status=status,
    )


def _respond(
    request: AuthenticatedHttpRequest, transfer: Transfer, *, error: str = '', status: int = 200
) -> HttpResponse:
    """The htmx conventions from `CLAUDE.md`: on success, the updated row partial for htmx, a
    plain redirect back to the dashboard otherwise. A non-htmx request (JS disabled, or a script)
    gets the same redirect either way -- there's no partial to swap in without htmx, so a
    validation error is surfaced as a flashed message instead (`core/base.html` renders
    `messages`)."""
    if not is_htmx(request):
        if error:
            messages.error(request, error)
        return redirect('file_transfer:dashboard')
    return _row_response(request, transfer, error=error, status=status)


@login_required
@require_POST
def disable(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    try:
        services.disable_transfer(transfer)
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)
    return _respond(request, transfer)


@login_required
@require_POST
def reenable(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    try:
        services.reenable_transfer_action(transfer)
    except (ValidationError, InsufficientCreditsError) as exc:
        message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
        return _respond(request, transfer, error=message, status=422)
    return _respond(request, transfer)


@login_required
@require_POST
def delete_now(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    try:
        services.delete_transfer_now(transfer)
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)
    return _respond(request, transfer)


@login_required
@require_POST
def update_settings(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    form = TransferSettingsActionForm(request.POST)
    if not form.is_valid():
        return _respond(request, transfer, error=_form_error_text(form), status=422)

    try:
        services.set_expiry(
            transfer, form.cleaned_data['expiry_choice'], form.cleaned_data.get('expiry_date')
        )
        services.set_max_downloads(transfer, form.cleaned_data.get('max_downloads'))
        # A blank password field means "leave it unchanged" -- the hash can't be pre-filled into
        # the input, so there's no way to tell "the owner cleared it" from "the owner didn't
        # touch it" other than a separate, explicit checkbox (see `TransferSettingsActionForm`).
        if form.cleaned_data.get('remove_password'):
            services.remove_password(transfer)
        elif form.cleaned_data.get('password'):
            services.set_password(transfer, form.cleaned_data['password'])
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)

    return _respond(request, transfer)


@login_required
@require_POST
def add_recipients(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    form = AddRecipientsForm(request.POST)
    if not form.is_valid():
        return _respond(request, transfer, error=_form_error_text(form), status=422)

    try:
        services.add_recipients(transfer, form.cleaned_data['recipients'])
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)

    return _respond(request, transfer)


@login_required
@require_POST
def resend_recipient(
    request: AuthenticatedHttpRequest, transfer_id: str, recipient_id: str
) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    recipient = get_object_or_404(TransferRecipient, id=recipient_id, transfer=transfer)
    try:
        services.resend_recipient_email(recipient)
    except ValidationError as exc:
        return _respond(request, transfer, error=exc.messages[0], status=422)
    return _respond(request, transfer)
