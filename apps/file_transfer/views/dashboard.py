"""The logged-in dashboard (spec section 6): list with an active/ended filter, and per-transfer
actions. Actions re-render the transfer's row (htmx conventions from `CLAUDE.md`: 422 + the same
partial on validation errors, the updated partial on success). Errors -- whether a form's or a
service's `ValidationError`/`InsufficientCreditsError` -- are surfaced as one plain message in the
row rather than per-field, since the row's inputs reflect the transfer's current (still valid)
state rather than the just-submitted one.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.forms import Form
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError

from .. import services
from ..forms import AddRecipientsForm, TransferSettingsActionForm
from ..models import ENDED_STATUSES, Transfer, TransferRecipient

ACTIVE_FILTER = 'active'
ENDED_FILTER = 'ended'


def _transfers_for(user, filter_value: str):
    qs = (
        Transfer.objects.filter(owner=user)
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


@login_required
@require_POST
def disable(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    try:
        services.disable_transfer(transfer)
    except ValidationError as exc:
        return _row_response(request, transfer, error=exc.messages[0], status=422)
    return _row_response(request, transfer)


@login_required
@require_POST
def reenable(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    try:
        services.reenable_transfer_action(transfer)
    except (ValidationError, InsufficientCreditsError) as exc:
        message = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
        return _row_response(request, transfer, error=message, status=422)
    return _row_response(request, transfer)


@login_required
@require_POST
def delete_now(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    try:
        services.delete_transfer_now(transfer)
    except ValidationError as exc:
        return _row_response(request, transfer, error=exc.messages[0], status=422)
    return _row_response(request, transfer)


@login_required
@require_POST
def update_settings(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    form = TransferSettingsActionForm(request.POST)
    if not form.is_valid():
        return _row_response(request, transfer, error=_form_error_text(form), status=422)

    try:
        services.set_expiry(
            transfer, form.cleaned_data['expiry_choice'], form.cleaned_data.get('expiry_date')
        )
        services.set_max_downloads(transfer, form.cleaned_data.get('max_downloads'))
        services.set_password(transfer, form.cleaned_data.get('password', ''))
    except ValidationError as exc:
        return _row_response(request, transfer, error=exc.messages[0], status=422)

    return _row_response(request, transfer)


@login_required
@require_POST
def add_recipients(request: AuthenticatedHttpRequest, transfer_id: str) -> HttpResponse:
    transfer = _owned_transfer(request, transfer_id)
    form = AddRecipientsForm(request.POST)
    if not form.is_valid():
        return _row_response(request, transfer, error=_form_error_text(form), status=422)

    try:
        services.add_recipients(transfer, form.cleaned_data['recipients'])
    except ValidationError as exc:
        return _row_response(request, transfer, error=exc.messages[0], status=422)

    return _row_response(request, transfer)


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
        return _row_response(request, transfer, error=exc.messages[0], status=422)
    return _row_response(request, transfer)
