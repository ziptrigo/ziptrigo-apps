"""Exports (issue #326): the "Download files" dialog (opened from the account page's "App actions",
`todo/partials/account_actions.html`), the quick XLSX, the list of exports, one export's page (with
a download button per zip file) and the actions on it. The twin of Receipts' `views.exports`.

The dialog is a fragment fetched into `#todo-export-modal`. It carries the "Task created" range
chosen on the account page as query parameters (`GET`) and as hidden fields (`POST`), so the files
of the tasks created in that range are what is exported. Credits are never spent unseen: the dialog
states the cost, and the form posts it back (`confirm_cost`); if it is no longer true
(`ExportCostChangedError`) the dialog comes back with the new total and nothing was charged. A
refusal is a 422 that re-renders the form in place.

Another user's export is a 404. A deleted one too.
"""

import logging
import tempfile
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.billing.services import InsufficientCreditsError
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx

from ..forms import ExportForm, parse_range
from ..models import ExportStatus, TodoExport
from ..services.account_actions import QUERY_PREFIX
from ..services.errors import AccountFrozenError
from ..services.export_range import ExportRange
from ..services.export_workbook import XLSX_CONTENT_TYPE
from ..services.exports import (
    ExportCostChangedError,
    ExportInProgressError,
    Prepared,
    build_xlsx,
    check_export,
    create_export,
    delete_export,
    part_download_url,
    part_name,
    prepare_export,
)
from .common import render_page

logger = logging.getLogger(__name__)

#: Automatic refreshes of the "preparing" panel (every 3 s, about two minutes), after which an
#: idle tab stops asking; the panel then says to reload.
_MAX_AUTO_POLLS = 40

PAGE_SIZE = 25

#: Where the dialog's 429 and refusals are shown.
DIALOG_SLOT = '#todo-export-msg'


def owned_export(request: AuthenticatedHttpRequest, export_id: object) -> TodoExport:
    """The requester's export (not a deleted one), or a 404, never a 403."""
    return get_object_or_404(
        TodoExport.objects.exclude(status=ExportStatus.DELETED),
        pk=export_id,
        user=request.user,
    )


# -- The quick XLSX -------------------------------------------------------------------------------


@login_required
@require_GET
def export_xlsx(request: AuthenticatedHttpRequest) -> HttpResponse | StreamingHttpResponse:
    """The tasks created in the chosen range, as a spreadsheet (free, nothing stored): `tasks.xlsx`
    with a Tasks and a Comments sheet, file names in the files columns. Parse first, charge after,
    like Receipts' CSV: this form's values are lenient (a bad one falls back to the default), but
    the order is kept so the two can't drift apart."""
    chosen = parse_range(request.GET)
    limited = ratelimit.hit_user(request.user, 'TODO_EXPORT_XLSX_USER')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    # The workbook is a zip itself, so it is written to a real file (spilling to disk when big)
    # and streamed from there; closing the response removes it.
    target = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    try:
        build_xlsx(request.user, chosen, target)
    except Exception:
        target.close()  # only the response closes it otherwise: don't wait for the collector
        raise
    target.seek(0)
    response = FileResponse(
        target,
        as_attachment=True,
        filename=f'tasks-{user_today(request.user).isoformat()}.xlsx',
        content_type=XLSX_CONTENT_TYPE,
    )
    response['Cache-Control'] = 'private, no-store'
    return response


# -- The dialog ---------------------------------------------------------------------------------


def _dialog_context(
    request: AuthenticatedHttpRequest,
    chosen: ExportRange,
    *,
    prepared: Prepared | None = None,
    errors: list[str] | None = None,
    extra_top_up: bool = False,
) -> dict:
    """The dialog's context. `prepared` is what the request already read (every refusal of a
    confirm has it, so the files are not read again); without it they are read here."""
    quote = (prepared or prepare_export(request.user, chosen)).quote
    return {
        'quote': quote,
        'filter_lines': chosen.describe(request.user),
        'filter_params': sorted(chosen.params.items()),
        'errors': errors or [],
        'top_up': extra_top_up or (quote.cost > quote.balance),
        'can_submit': quote.file_count > 0 and not quote.frozen,
    }


def account_actions_url(chosen: ExportRange | None = None) -> str:
    """Where the dialog's buttons are: the account page's Apps section, with the range of `chosen`
    preselected (under the block's `todo_` prefix, `services.account_actions`; dates with no
    `date_range` are read there as Custom)."""
    query = ''
    if chosen is not None:
        query = urlencode({f'{QUERY_PREFIX}{key}': value for key, value in chosen.params.items()})
    return f'{reverse("accounts:account")}{"?" + query if query else ""}#apps'


@login_required
@require_http_methods(['GET', 'POST'])
def export_new(request: AuthenticatedHttpRequest) -> HttpResponse:
    """GET: the dialog (a fragment; opened directly it goes to the account page). POST: confirm it
    and start the export."""
    if request.method == 'GET':
        if not is_htmx(request):
            return redirect(account_actions_url())
        chosen = parse_range(request.GET)
        # The dialog reads every file in the range to quote it: bounded, loosely.
        limited = ratelimit.hit_user(request.user, 'TODO_EXPORT_DIALOG_USER')
        if not limited.allowed:
            return ratelimit.web_response(request, limited)
        return render(request, 'todo/partials/export_modal.html', _dialog_context(request, chosen))
    return _confirm(request)


def _refused(
    request: AuthenticatedHttpRequest,
    chosen: ExportRange,
    lines: list[str],
    *,
    prepared: Prepared | None = None,
    top_up: bool = False,
) -> HttpResponse:
    """The form again with what was wrong (htmx swaps it in place); without htmx, flash messages
    and back to the account page, with the range that was chosen."""
    if is_htmx(request):
        context = _dialog_context(
            request, chosen, prepared=prepared, errors=lines, extra_top_up=top_up
        )
        return render(request, 'todo/partials/export_form.html', context, status=422)
    for line in lines:
        messages.error(request, line)
    return redirect(account_actions_url(chosen))


def _confirm(request: AuthenticatedHttpRequest) -> HttpResponse:
    chosen = parse_range(request.POST)
    form = ExportForm(request.POST)
    if not form.is_valid():
        lines = [message for errors in form.errors.values() for message in errors]
        return _refused(request, chosen, lines)
    confirmed = form.cleaned_data['confirm_cost']
    if confirmed is None:
        return _refused(request, chosen, ['Confirm the cost to continue.'])

    # Reading every file is bounded like the dialog's quote: over its budget nothing is read, and
    # a refusal below (which did read them) spends one hit of it.
    limited = ratelimit.peek_user(request.user, 'TODO_EXPORT_DIALOG_USER')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget=DIALOG_SLOT)

    # The files are read and planned once, here: every refusal below shows the dialog from them
    # and `create_export` is handed the same plan (and checks again what could have changed).
    prepared = prepare_export(request.user, chosen)

    # Every refusal that needs no write is told before the rate limit is spent. (The frozen
    # error is a `ValidationError` too: it is caught first.)
    try:
        check_export(request.user, chosen, expected_cost=confirmed, prepared=prepared)
    except ExportCostChangedError as exc:
        ratelimit.hit_user(request.user, 'TODO_EXPORT_DIALOG_USER')
        return _refused(request, chosen, [exc.MESSAGE], prepared=prepared)
    except InsufficientCreditsError:
        ratelimit.hit_user(request.user, 'TODO_EXPORT_DIALOG_USER')
        return _refused(
            request,
            chosen,
            ["You don't have enough credits for this."],
            prepared=prepared,
            top_up=True,
        )
    except AccountFrozenError as exc:
        ratelimit.hit_user(request.user, 'TODO_EXPORT_DIALOG_USER')
        return _refused(request, chosen, list(exc.messages), prepared=prepared, top_up=True)
    except ValidationError as exc:
        ratelimit.hit_user(request.user, 'TODO_EXPORT_DIALOG_USER')
        return _refused(request, chosen, list(exc.messages), prepared=prepared)

    limited = ratelimit.hit_user(request.user, 'TODO_EXPORT_FILES_USER')
    if not limited.allowed:
        return ratelimit.web_response(request, limited, retarget=DIALOG_SLOT)

    # A refusal from here on means something changed since the files were read, so the dialog
    # reads them again (no `prepared`).
    try:
        export = create_export(request.user, chosen, expected_cost=confirmed, prepared=prepared)
    except ExportCostChangedError as exc:
        return _refused(request, chosen, [exc.MESSAGE])
    except InsufficientCreditsError:
        return _refused(request, chosen, ["You don't have enough credits for this."], top_up=True)
    except AccountFrozenError as exc:
        return _refused(request, chosen, list(exc.messages), top_up=True)
    except (ExportInProgressError, ValidationError) as exc:
        return _refused(request, chosen, list(exc.messages))
    messages.success(
        request,
        "We're preparing your zip files. We'll tell you here and by email when they are ready.",
    )
    return hx_redirect(request, reverse('todo:export-detail', args=[export.pk]))


# -- The pages ----------------------------------------------------------------------------------


@login_required
@require_GET
def exports_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    queryset = (
        TodoExport.objects.filter(user=request.user)
        .exclude(status=ExportStatus.DELETED)
        .order_by('-created_at')
    )
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))
    return render_page(
        request,
        'todo/exports.html',
        {'exports': list(page_obj.object_list), 'page_obj': page_obj},
        nav='settings',
    )


def _panel_context(export: TodoExport, polls: int) -> dict:
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
            f'{reverse("todo:export-panel", args=[export.pk])}?polls={polls + 1}'
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
    return render_page(
        request, 'todo/export.html', _panel_context(export, _polls(request)), nav='settings'
    )


@login_required
@require_GET
def export_panel(request: AuthenticatedHttpRequest, export_id: str) -> HttpResponse:
    """The export's status panel (polled while it is being prepared). Opened directly it goes to
    the page."""
    export = owned_export(request, export_id)
    if not is_htmx(request):
        return redirect('todo:export-detail', export_id=export.pk)
    return render(
        request, 'todo/partials/export_panel.html', _panel_context(export, _polls(request))
    )


@login_required
@require_GET
def export_part_download(
    request: AuthenticatedHttpRequest, export_id: str, number: int
) -> HttpResponse:
    """Redirect to a short-lived "Save As" URL of one zip file."""
    export = owned_export(request, export_id)
    limited = ratelimit.hit_user(request.user, 'TODO_EXPORT_DOWNLOAD_USER')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)
    try:
        url = part_download_url(export, number)
    except ValidationError as exc:  # also the frozen account's
        messages.error(request, exc.messages[0])
        return redirect('todo:export-detail', export_id=export.pk)
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
        return redirect('todo:export-detail', export_id=export.pk)
    messages.success(request, 'The export was deleted.')
    return redirect('todo:exports')
