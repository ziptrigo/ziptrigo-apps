"""Reports (issue #351): `/receipts/reports/`, receipts by vendor or by category in a period, and
its three downloads (`export.xlsx`, `export.csv`, `export.pdf`).

The filters are a GET form that htmx re-fetches into `#report`; the response's `HX-Push-Url` puts
the *page* URL in the address bar, so a reload or a shared link renders the full page, and the
partial opened directly redirects to it (the dashboard's list works the same way). The downloads
are plain links carrying the same query string. They are free; `RECEIPTS_REPORT_USER` bounds them
(charged once the filters are parsed). Another user's receipts never appear: every query is the
requester's.
"""

import tempfile

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponse, HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.core import ratelimit
from apps.core.htmx import is_htmx

from ..forms import DATE_RANGES, ParsedReport, parse_report
from ..services import report_files
from ..services.reports import GROUP_CATEGORY, GROUP_VENDOR, build_report, describe_filters
from .common import render_page

GROUP_TABS = (
    (GROUP_VENDOR, 'By vendor', 'fas fa-store'),
    (GROUP_CATEGORY, 'By category', 'fas fa-tags'),
)

_DOWNLOADS = (
    ('XLSX', 'fas fa-file-excel text-green-700 dark:text-green-400', 'receipts:report-xlsx'),
    ('CSV', 'fas fa-file-csv text-sage-500 dark:text-sage-300', 'receipts:report-csv'),
    ('PDF', 'fas fa-file-pdf text-red-700 dark:text-red-400', 'receipts:report-pdf'),
)


def _with_query(url: str, query: str) -> str:
    return f'{url}?{query}' if query else url


def _row_link(parsed: ParsedReport, key: int | None) -> str:
    """The dashboard filtered by this vendor (or category) and the report's dates, `''` for the
    receipts that have no vendor (the dashboard has no "no vendor" filter)."""
    dashboard = reverse('receipts:dashboard')
    if parsed.group == GROUP_VENDOR:
        if key is None:
            return ''
        return _with_query(dashboard, parsed.dashboard_query(vendor=str(key)))
    category = 'none' if key is None else str(key)
    return _with_query(dashboard, parsed.dashboard_query(category=category))


def _context(request: AuthenticatedHttpRequest, parsed: ParsedReport) -> dict:
    report = build_report(request.user, parsed.filters)
    tabs = [
        {
            'key': key,
            'label': label,
            'icon': icon,
            'active': key == parsed.group,
            'href': _with_query(reverse('receipts:reports'), parsed.query(group=key)),
            'list_url': _with_query(reverse('receipts:report-list'), parsed.query(group=key)),
        }
        for key, label, icon in GROUP_TABS
    ]
    download_query = parsed.download_query()
    return {
        'parsed': parsed,
        'report': report,
        'rows': [{'row': row, 'url': _row_link(parsed, row.key)} for row in report.rows],
        'tabs': tabs,
        'sentence': describe_filters(parsed.filters),
        'by_vendor': parsed.group == GROUP_VENDOR,
        'date_range_options': [{'value': v, 'label': label} for v, label in DATE_RANGES],
        'dates_disabled': parsed.date_range != 'custom',
        'form_values': parsed.params,
        'report_url': reverse('receipts:reports'),
        'list_url': reverse('receipts:report-list'),
        'downloads': [
            {'label': label, 'icon': icon, 'url': _with_query(reverse(name), download_query)}
            for label, icon, name in _DOWNLOADS
        ],
    }


@login_required
@require_GET
def reports_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return render_page(request, 'receipts/reports.html', _context(request, parse_report(request)))


@login_required
@require_GET
def report_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The report partial. Opened directly (not by htmx) it goes to the full page instead."""
    parsed = parse_report(request)
    if not is_htmx(request):
        return redirect(_with_query(reverse('receipts:reports'), parsed.query()))
    context = _context(request, parsed)
    context['oob'] = True
    # Plain `render`, not `render_page`: the partial needs neither the account nor the banner.
    response = render(request, 'receipts/partials/report_content.html', context)
    response['HX-Push-Url'] = _with_query(reverse('receipts:reports'), parsed.query())
    return response


# -- Downloads ------------------------------------------------------------------------------------


def _download(request: AuthenticatedHttpRequest, extension: str) -> HttpResponseBase:
    # Parse first, charge after: the filters are lenient (a bad value falls back to the default),
    # but the order is kept like the other exports so a refusal can never spend the budget.
    parsed = parse_report(request)
    limited = ratelimit.hit_user(request.user, 'RECEIPTS_REPORT_USER')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    report = build_report(request.user, parsed.filters)
    name = report_files.file_name(report, extension, user_today(request.user))
    if extension == 'csv':
        response = HttpResponse(
            report_files.report_csv(report), content_type=report_files.CSV_CONTENT_TYPE
        )
        response['Content-Disposition'] = f'attachment; filename="{name}"'
    else:
        content_type, write = {
            'xlsx': (report_files.XLSX_CONTENT_TYPE, report_files.report_xlsx),
            'pdf': (report_files.PDF_CONTENT_TYPE, report_files.report_pdf),
        }[extension]
        # A spooled temp file: closing the response removes it.
        target = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        try:
            write(report, target)
        except Exception:
            target.close()
            raise
        target.seek(0)
        response = FileResponse(
            target, as_attachment=True, filename=name, content_type=content_type
        )
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def report_csv(request: AuthenticatedHttpRequest) -> HttpResponseBase:
    return _download(request, 'csv')


@login_required
@require_GET
def report_xlsx(request: AuthenticatedHttpRequest) -> HttpResponseBase:
    return _download(request, 'xlsx')


@login_required
@require_GET
def report_pdf(request: AuthenticatedHttpRequest) -> HttpResponseBase:
    return _download(request, 'pdf')
