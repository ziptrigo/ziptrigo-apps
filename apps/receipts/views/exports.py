"""Exports (issue #315): the "Download files" dialog (opened from the account page's "App actions",
`receipts/partials/account_actions.html`), the list of exports, one export's page (with a download
button per zip file) and the actions on it.

The dialog is a fragment fetched into `#export-modal` (like the vendor rename dialog). It carries the
receipt-date range chosen on the account page as query parameters (`GET`) and as hidden fields
(`POST`), so the receipts in that range are what is exported. Credits are never spent unseen: the dialog states the cost, and the
form posts it back (`confirm_cost`); if it is no longer true (`ExportCostChangedError`) the dialog
comes back with the new total and nothing was charged. A refusal is a 422 that re-renders the form
in place.

Another user's export is a 404. A deleted one too.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import InsufficientCreditsError
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import ExportForm, parse_filters
from ..models import ExportLayout, ExportStatus, ReceiptExport
from ..services import (
    AccountFrozenError,
    ExportCostChangedError,
    ExportInProgressError,
    check_export,
    create_export,
    delete_export,
    part_download_url,
    prepare_export,
)
from ..services.exports import Prepared, describe_filters, part_name
from .common import render_page
from .dashboard import resolve_vendor_filter

logger = logging.getLogger(__name__)

#: Automatic refreshes of the "preparing" panel (every 3 s, about two minutes), after which an
#: idle tab stops asking; the panel then says to reload.
_MAX_AUTO_POLLS = 40

PAGE_SIZE = 25

#: What the two layouts look like, for the dialog.
LAYOUT_EXAMPLES = {
    ExportLayout.YEAR_VENDOR: '2026/Acme/invoice.pdf',
    ExportLayout.VENDOR_YEAR: 'Acme/2026/invoice.pdf',
}


def owned_export(request: AuthenticatedHttpRequest, export_id: object) -> ReceiptExport:
    """The requester's export (not a deleted one), or a 404, never a 403."""
    return get_object_or_404(
        ReceiptExport.objects.exclude(status=ExportStatus.DELETED),
        pk=export_id,
        user=request.user,
    )


# -- The dialog ---------------------------------------------------------------------------------


def _dialog_context(
    request: AuthenticatedHttpRequest,
    parsed,
    layout: str,
    *,
    prepared: Prepared | None = None,
    errors: list[str] | None = None,
    extra_top_up: bool = False,
) -> dict:
    """The dialog's context. `prepared` is what the request already read (every refusal of a
    confirm has it, so the list is not read again); without it the list is read here."""
    quote = (prepared or prepare_export(request.user, parsed.filters, layout)).quote
    return {
        'quote': quote,
        'layout': layout,
        'layouts': [
            {'value': value, 'label': label, 'example': LAYOUT_EXAMPLES[value]}
            for value, label in ExportLayout.choices
        ],
        'filter_lines': describe_filters(request.user, parsed.filters),
        'filter_params': sorted(parsed.params.items()),
        'errors': errors or [],
        'top_up': extra_top_up or (quote.cost > quote.balance),
        'can_submit': quote.file_count > 0 and not quote.frozen,
    }


def _account_actions_url() -> str:
    """Where the dialog's buttons are: the account page's Apps section."""
    return f'{reverse("accounts:account")}#apps'


def _layout_of(value: object) -> str:
    return str(value) if value in ExportLayout.values else str(ExportLayout.YEAR_VENDOR)


@login_required
@require_http_methods(['GET', 'POST'])
def export_new(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the dialog (a fragment; opened directly it goes to the account page). POST: confirm it
    and start the export."""
    if request.method == 'GET':
        parsed = parse_filters(request)
        resolve_vendor_filter(request, parsed)
        if not is_htmx(request):
            return redirect(_account_actions_url())
        # The dialog reads the whole filtered list to quote it: bounded, loosely.
        limited = ratelimit.hit_user(request.user, 'RECEIPTS_EXPORT_DIALOG_USER')
        if not limited.allowed:
            return ratelimit.web_response(request, limited)
        context = _dialog_context(request, parsed, _layout_of(request.GET.get('layout')))
        return render(request, 'receipts/partials/export_modal.html', context)
    return _confirm(request)


def _refused(
    request: AuthenticatedHttpRequest,
    parsed,
    layout: str,
    messages_list: list[str],
    *,
    prepared: Prepared | None = None,
    top_up: bool = False,
) -> HttpResponse:
    """The form again with what was wrong (htmx swaps it in place); without htmx, flash messages
    and back to the account page."""
    if is_htmx(request):
        context = _dialog_context(
            request,
            parsed,
            layout,
            prepared=prepared,
            errors=messages_list,
            extra_top_up=top_up,
        )
        return render(request, 'receipts/partials/export_form.html', context, status=422)
    for line in messages_list:
        messages.error(request, line)
    return redirect(_account_actions_url())


def _confirm(request: AuthenticatedHttpRequest) -> HttpResponse:
    parsed = parse_filters(request, request.POST)
    resolve_vendor_filter(request, parsed)
    form = ExportForm(request.POST)
    layout = _layout_of(request.POST.get('layout'))
    if not form.is_valid():
        lines = [message for errors in form.errors.values() for message in errors]
        return _refused(request, parsed, layout, lines)
    layout = form.cleaned_data['layout']
    confirmed = form.cleaned_data['confirm_cost']
    if confirmed is None:
        return _refused(request, parsed, layout, ['Confirm the cost to continue.'])

    # Reading the whole list is bounded like the dialog's quote: over its budget nothing is read,
    # and a refusal below (which did read it) spends one hit of it.
    limited = ratelimit.peek_user(request.user, 'RECEIPTS_EXPORT_DIALOG_USER')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget='#export-msg')

    # The list is read and planned once, here: every refusal below shows the dialog from it and
    # `create_export` is handed the same plan (and checks again what could have changed).
    prepared = prepare_export(request.user, parsed.filters, layout)

    # Every refusal that needs no write is told before the rate limit is spent.
    try:
        check_export(
            request.user,
            parsed.filters,
            layout,
            expected_cost=confirmed,
            prepared=prepared,
        )
    except ExportCostChangedError as exc:
        ratelimit.hit_user(request.user, 'RECEIPTS_EXPORT_DIALOG_USER')
        return _refused(request, parsed, layout, [f'{exc.MESSAGE}'], prepared=prepared)
    except InsufficientCreditsError:
        ratelimit.hit_user(request.user, 'RECEIPTS_EXPORT_DIALOG_USER')
        return _refused(
            request,
            parsed,
            layout,
            ["You don't have enough credits for this."],
            prepared=prepared,
            top_up=True,
        )
    except AccountFrozenError as exc:
        ratelimit.hit_user(request.user, 'RECEIPTS_EXPORT_DIALOG_USER')
        return _refused(request, parsed, layout, [exc.message], prepared=prepared, top_up=True)
    except ValidationError as exc:
        ratelimit.hit_user(request.user, 'RECEIPTS_EXPORT_DIALOG_USER')
        return _refused(request, parsed, layout, list(exc.messages), prepared=prepared)

    limited = ratelimit.hit_user(request.user, 'RECEIPTS_EXPORT_FILES_USER')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget='#export-msg')

    # A refusal from here on means something changed since the list was read, so the dialog reads
    # it again (no `prepared`).
    try:
        export = create_export(
            request.user,
            parsed.filters,
            layout,
            expected_cost=confirmed,
            prepared=prepared,
        )
    except ExportCostChangedError as exc:
        return _refused(request, parsed, layout, [exc.MESSAGE])
    except InsufficientCreditsError:
        return _refused(
            request, parsed, layout, ["You don't have enough credits for this."], top_up=True
        )
    except AccountFrozenError as exc:
        return _refused(request, parsed, layout, [exc.message], top_up=True)
    except (ExportInProgressError, ValidationError) as exc:
        return _refused(request, parsed, layout, list(exc.messages))
    messages.success(
        request,
        "We're preparing your zip files. We'll tell you here and by email when they are ready.",
    )
    return hx_redirect(request, reverse('receipts:export-detail', args=[export.pk]))


# -- The pages ----------------------------------------------------------------------------------


@login_required
@require_GET
def exports_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    queryset = (
        ReceiptExport.objects.filter(user=request.user)
        .exclude(status=ExportStatus.DELETED)
        .order_by('-created_at')
    )
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))
    return render_page(
        request,
        'receipts/exports.html',
        {'exports': list(page_obj.object_list), 'page_obj': page_obj},
    )


def _panel_context(export: ReceiptExport, polls: int) -> dict:
    parts = list(export.parts.order_by('number'))
    built = sum(1 for part in parts if part.built_at is not None)
    ready = export.status == ExportStatus.READY
    return {
        'export': export,
        'parts': [
            {
                'number': part.number,
                'name': part_name(export, part.number),
                'size_bytes': part.size_bytes,
                'file_count': part.file_count,
                'built': part.built_at is not None,
            }
            for part in parts
        ]
        if ready
        else [],
        'built_count': built,
        'polls': polls,
        'poll_url': (
            f'{reverse("receipts:export-panel", args=[export.pk])}?polls={polls + 1}'
            if export.status == ExportStatus.BUILDING and polls < _MAX_AUTO_POLLS
            else ''
        ),
        'filter_lines': (export.filters or {}).get('summary', []),
        'files_in_zips': export.file_count - export.skipped_count,
    }


def _polls(request: AuthenticatedHttpRequest) -> int:
    try:
        return max(int(request.GET.get('polls', '0')), 0)
    except ValueError:
        return 0


@login_required
@require_GET
def export_detail(request: AuthenticatedHttpRequest, export_id: str) -> HttpResponse:
    export = owned_export(request, export_id)
    return render_page(request, 'receipts/export.html', _panel_context(export, _polls(request)))


@login_required
@require_GET
def export_panel(request: AuthenticatedHttpRequest, export_id: str) -> HttpResponse:
    """The export's status panel (polled while it is being prepared). Opened directly it goes to
    the page."""
    export = owned_export(request, export_id)
    if not is_htmx(request):
        return redirect('receipts:export-detail', export_id=export.pk)
    return render(
        request, 'receipts/partials/export_panel.html', _panel_context(export, _polls(request))
    )


@login_required
@require_GET
def export_part_download(
    request: AuthenticatedHttpRequest, export_id: str, number: int
) -> HttpResponse:
    """Redirect to a short-lived "Save As" URL of one zip file."""
    export = owned_export(request, export_id)
    limited = ratelimit.hit_user(request.user, 'RECEIPTS_EXPORT_DOWNLOAD_USER')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)
    try:
        url = part_download_url(export, number)
    except AccountFrozenError as exc:
        messages.error(request, exc.message)
        return redirect('receipts:export-detail', export_id=export.pk)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect('receipts:export-detail', export_id=export.pk)
    response = redirect(url)
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def export_delete(request: AuthenticatedHttpRequest, export_id: str) -> HttpResponse:
    export = owned_export(request, export_id)
    try:
        delete_export(export)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect('receipts:export-detail', export_id=export.pk)
    messages.success(request, 'The export was deleted.')
    return redirect('receipts:exports')
