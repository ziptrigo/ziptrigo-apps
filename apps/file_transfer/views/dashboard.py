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
from django.db import transaction
from django.db.models import Count, Prefetch
from django.forms import Form
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError
from apps.core.htmx import is_htmx

from .. import services
from ..forms import AddRecipientsForm, TransferSettingsActionForm
from ..models import ENDED_STATUSES, DownloadEvent, Transfer, TransferRecipient, TransferStatus

#: How many rows of a transfer's per-download log the row partial shows (spec section 6: "a
#: per-download log is available per transfer") -- fetched via `Prefetch` below so a transfer with
#: thousands of downloads never pulls its whole history into memory just to render a row.
_DOWNLOAD_LOG_LIMIT = 20

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
        # `download_events` is prefetched separately, bounded to the log's own display limit and
        # with its `file` selected up front -- otherwise the row template's `event.file.name`
        # would fire one extra query per shown event (an N+1), and a heavily-downloaded transfer
        # would prefetch its entire download history just to show the newest handful of rows.
        .annotate(download_events_count=Count('download_events', distinct=True))
        .prefetch_related(
            'files',
            'recipients',
            # `to_attr` is required for a *sliced* `Prefetch` queryset: Django's own prefetch
            # machinery needs to further filter this queryset by parent id, which a slice
            # otherwise forbids ("Cannot filter a query once a slice has been taken") -- `to_attr`
            # sidesteps that by populating a plain list attribute instead of the default related
            # manager's cache.
            Prefetch(
                'download_events',
                queryset=DownloadEvent.objects.select_related('file').order_by('-created_at')[
                    :_DOWNLOAD_LOG_LIMIT
                ],
                to_attr='recent_download_events',
            ),
        )
        .order_by('-created_at')
    )
    if filter_value == ENDED_FILTER:
        return qs.filter(status__in=ENDED_STATUSES)
    return qs.exclude(status__in=ENDED_STATUSES)


def _form_error_text(form: Form) -> str:
    return ' '.join(message for field_errors in form.errors.values() for message in field_errors)


def _resumable_draft(user) -> Transfer | None:
    """The owner's most recent draft that already has at least one file on it, if any -- surfaced
    on the dashboard as a "resume your unfinished upload" link, since drafts are otherwise never
    shown here (`_UNLISTED_STATUSES`) and opening the send page from the nav (rather than a literal
    page reload, which carries its own `?resume=`) would otherwise just start a fresh, empty one
    (`services.get_or_create_draft`), stranding the in-progress one until `cleanup_drafts` reaps
    it."""
    return (
        Transfer.objects.filter(owner=user, status=TransferStatus.DRAFT)
        .annotate(_file_count=Count('files'))
        .filter(_file_count__gt=0)
        .order_by('-created_at')
        .first()
    )


@login_required
@require_GET
def dashboard(request: AuthenticatedHttpRequest) -> HttpResponse:
    filter_value = request.GET.get('filter', ACTIVE_FILTER)
    context = {
        'transfers': _transfers_for(request.user, filter_value),
        'filter': filter_value,
        'resumable_draft': _resumable_draft(request.user),
    }
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
        # Atomic for the same reason the JWT API's `update_transfer` is: several separate
        # `UPDATE`s from one submit, and a later one failing must not leave the earlier ones
        # committed as a silent partial update.
        with transaction.atomic():
            services.set_expiry(
                transfer, form.cleaned_data['expiry_choice'], form.cleaned_data.get('expiry_date')
            )
            services.set_max_downloads(transfer, form.cleaned_data.get('max_downloads'))
            # A blank password field means "leave it unchanged" -- the hash can't be pre-filled
            # into the input, so there's no way to tell "the owner cleared it" from "the owner
            # didn't touch it" other than a separate, explicit checkbox (see
            # `TransferSettingsActionForm`).
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
