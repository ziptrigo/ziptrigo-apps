"""The dashboard (`/receipts/`): inbox address, status tabs with counts, filters, the list and
the CSV export.

The page and `receipt_list` (its htmx partial) are built from the same context. A filter or tab
change re-fetches the partial into `#receipt-list` and the response's `HX-Push-Url` puts the
*dashboard* URL (not the partial's) in the address bar, so a reload or a shared link renders the
full page. Rows still being processed refresh the list by themselves, `_MAX_AUTO_POLLS` times.
"""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.htmx import is_htmx

from ..forms import DATE_RANGES, PAGE_SIZE, TABS, ParsedFilters, parse_filters
from ..models import IN_PROGRESS_STATUSES, ReceiptCategory, ReceiptStatus, Vendor
from ..services import (
    dashboard_counts,
    filter_receipts,
    inbox_address_for,
    iter_csv,
    vendor_counts,
)
from .common import render_page

#: Automatic refreshes of the list while something is still being processed (every 3 s, so about
#: two minutes), after which an idle tab stops asking; same idea as the zip build's polling.
_MAX_AUTO_POLLS = 40

_POLLED_STATUSES = (ReceiptStatus.RECEIVED, ReceiptStatus.PROCESSING)

_TAB_COUNT_KEYS = {
    'all': None,
    'needs_review': 'needs_review',
    'processing': 'in_progress',
    'failed': 'failed',
    'duplicates': 'duplicates',
}


def _with_query(url: str, query: str) -> str:
    return f'{url}?{query}' if query else url


def _sort_links(parsed: ParsedFilters) -> dict:
    """Header links: clicking the active column flips its direction, any other starts newest
    first. `Receipt date` and `Added` are the two sortable columns."""
    current = parsed.filters.sort
    links = {}
    for column in ('receipt_date', 'created_at'):
        active = current.lstrip('-') == column
        descending = current == f'-{column}'
        target = column if descending else f'-{column}'
        links[column] = {
            'active': active,
            'descending': descending,
            'query': parsed.query(sort=target),
        }
    return links


def _resolve_vendor_filter(
    request: AuthenticatedHttpRequest, parsed: ParsedFilters
) -> Vendor | None:
    """The vendor `?vendor=<id>` names, if it is one of the user's. Any other id (a vendor that
    was deleted or merged away since the link was made, someone else's) is dropped from the
    filters like any other value that doesn't validate, rather than showing an empty list."""
    if parsed.filters.vendor is None:
        return None
    vendor = Vendor.objects.filter(pk=parsed.filters.vendor, user=request.user).first()
    if vendor is None:
        parsed.filters.vendor = None
        parsed.params.pop('vendor', None)
    return vendor


def _list_context(request: AuthenticatedHttpRequest, parsed: ParsedFilters) -> dict:
    vendor_filter = _resolve_vendor_filter(request, parsed)
    queryset = filter_receipts(request.user, parsed.filters).select_related(
        'category', 'possible_duplicate_of'
    )
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))
    receipts = list(page_obj.object_list)

    counts = dashboard_counts(request.user)
    tabs = []
    for key, label in TABS:
        count_key = _TAB_COUNT_KEYS[key]
        query = parsed.query(status=None if key == 'all' else key)
        tabs.append(
            {
                'key': key,
                'label': label,
                'count': counts[count_key] if count_key else None,
                'active': parsed.tab == key,
                'href': _with_query(reverse('receipts:dashboard'), query),
                'list_url': _with_query(reverse('receipts:list'), query),
            }
        )

    try:
        polls = max(int(request.GET.get('polls', '0')), 0)
    except ValueError:
        polls = 0
    page_query = parsed.query(page=page_obj.number if page_obj.number > 1 else None)
    poll_url = ''
    # A receipt waiting for a retry won't change for minutes, so it doesn't keep the list polling.
    if polls < _MAX_AUTO_POLLS and any(
        r.status in _POLLED_STATUSES and not r.retry_scheduled for r in receipts
    ):
        poll_url = _with_query(
            reverse('receipts:list'),
            f'{page_query}&polls={polls + 1}' if page_query else f'polls={polls + 1}',
        )

    return {
        'receipts': receipts,
        'page_obj': page_obj,
        'parsed': parsed,
        'tabs': tabs,
        'counts': counts,
        'sort_links': _sort_links(parsed),
        'poll_url': poll_url,
        'page_query': page_query,
        'list_url': reverse('receipts:list'),
        'dashboard_url': reverse('receipts:dashboard'),
        'export_url': _with_query(reverse('receipts:export'), parsed.query()),
        'prev_query': parsed.query(page=page_obj.previous_page_number())
        if page_obj.has_previous()
        else '',
        'next_query': parsed.query(page=page_obj.next_page_number()) if page_obj.has_next() else '',
        'in_progress_statuses': IN_PROGRESS_STATUSES,
        'vendor_filter': vendor_filter,
        'clear_vendor_query': parsed.query(vendor=None),
        'vendors_to_vet': vendor_counts(request.user)['to_vet'],
    }


@login_required
@require_GET
@ensure_csrf_cookie
def dashboard(request: AuthenticatedHttpRequest) -> HttpResponse:
    parsed = parse_filters(request)
    context = _list_context(request, parsed)
    context.update(
        {
            'inbox_address': inbox_address_for(request.user),
            'categories': ReceiptCategory.objects.filter(user=request.user),
            'date_ranges': DATE_RANGES,
            'form_values': parsed.params,
        }
    )
    return render_page(request, 'receipts/dashboard.html', context)


@login_required
@require_GET
def receipt_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The list partial. Opened directly (not by htmx) it goes to the full page instead."""
    parsed = parse_filters(request)
    if not is_htmx(request):
        query = parsed.query(page=request.GET.get('page'))
        return redirect(_with_query(reverse('receipts:dashboard'), query))

    context = _list_context(request, parsed)
    context['oob_export'] = True
    # Plain `render`, not `render_page`: the partial needs neither the Receipts account nor the
    # frozen banner, and this is the hot path (a poll every 3 s, a request per search keystroke).
    response = render(request, 'receipts/partials/receipt_list.html', context)
    if 'polls' not in request.GET:
        response['HX-Push-Url'] = _with_query(reverse('receipts:dashboard'), context['page_query'])
    return response


@login_required
@require_GET
def export_csv(request: AuthenticatedHttpRequest) -> HttpResponse | StreamingHttpResponse:
    """The live receipts matching the current filters as CSV (the services build the file)."""
    # Parse first, charge after, like the API's export: its filters can be refused (a 400 that
    # must not spend the budget); this form's are lenient (a bad value falls back to the default),
    # but the order is kept so the two can't drift apart.
    parsed = parse_filters(request)
    _resolve_vendor_filter(request, parsed)
    limited = ratelimit.hit_user(request.user, 'RECEIPTS_EXPORT_USER')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    response = StreamingHttpResponse(
        iter_csv(request.user, parsed.filters), content_type='text/csv; charset=utf-8'
    )
    filename = f'receipts-{timezone.localdate().isoformat()}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    return response
