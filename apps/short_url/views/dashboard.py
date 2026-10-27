"""The dashboard (`/links/`): status tabs with counts, search, a tag filter, sorting and the list.

The page and `link_list` (its htmx partial) are built from the same context. A filter or tab change
re-fetches the partial into `#link-list` and the response's `HX-Push-Url` puts the *dashboard* URL
(not the partial's) in the address bar, so a reload or a shared link renders the full page.
"""

from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import is_htmx

from ..forms import PAGE_SIZE, STATUS_TABS, ParsedFilters, parse_filters
from ..services import blocklist, destination_host, display_state, enable_costs
from ..services.filters import filter_links, state_filter, visible_links
from .common import render_page

#: How many links `owner_tags` reads at most (the newest first), so the tag filter's cost stays
#: bounded however many links a user has. The hard cap on links makes this a ceiling, not a
#: usual limit.
TAG_SCAN_LIMIT = 1000


def _with_query(url: str, query: str) -> str:
    return f'{url}?{query}' if query else url


def tab_counts(request: AuthenticatedHttpRequest, now: datetime) -> dict[str, int]:
    """The number in each status tab (the "All" tab counts every link that isn't deleted)."""
    aggregates = {'all': Count('pk')}
    for key, _label in STATUS_TABS:
        if key != 'all':
            aggregates[key] = Count('pk', filter=state_filter(key, now))
    return visible_links(request.user).aggregate(**aggregates)


def owner_tags(request: AuthenticatedHttpRequest) -> list[str]:
    """The tags on the user's newest `TAG_SCAN_LIMIT` links, sorted, for the tag filter."""
    tags: set[str] = set()
    newest = visible_links(request.user).order_by('-created_at').values_list('tags', flat=True)
    for value in newest[:TAG_SCAN_LIMIT]:
        tags.update(value or [])
    return sorted(tags)


def _list_context(request: AuthenticatedHttpRequest, parsed: ParsedFilters) -> dict:
    now = timezone.now()
    queryset = filter_links(
        request.user, tab=parsed.tab, q=parsed.q, tag=parsed.tag, sort=parsed.sort, now=now
    )
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))
    links = list(page_obj.object_list)
    # What enabling each disabled row costs, so its menu can ask before charging.
    costs = enable_costs(request.user, links)
    # One query for the page's distinct hosts, so a link whose destination was blocked after it
    # was made shows "Not working" instead of "Active" (the same pill as its own page).
    blocked = blocklist.blocked_hosts(destination_host(link.destination) for link in links)
    for link in links:
        link.enable_cost = costs[link.pk]
        link.state = display_state(
            link, now=now, destination_blocked=destination_host(link.destination) in blocked
        )

    counts = tab_counts(request, now)
    tabs = []
    for key, label in STATUS_TABS:
        query = parsed.query(status=None if key == 'all' else key)
        tabs.append(
            {
                'key': key,
                'label': label,
                'count': counts[key],
                'active': parsed.tab == key,
                'href': _with_query(reverse('short_url:dashboard'), query),
                'list_url': _with_query(reverse('short_url:list'), query),
            }
        )

    descending = parsed.sort.startswith('-')
    sort_links = {}
    for column in ('created_at', 'click_count'):
        active = parsed.sort.lstrip('-') == column
        target = column if active and descending else f'-{column}'
        sort_links[column] = {
            'active': active,
            'descending': active and descending,
            'query': parsed.query(sort=target),
        }

    return {
        'links': links,
        'now': now,
        'page_obj': page_obj,
        'parsed': parsed,
        'tabs': tabs,
        'sort_links': sort_links,
        'list_url': reverse('short_url:list'),
        'dashboard_url': reverse('short_url:dashboard'),
        'page_query': parsed.query(page=page_obj.number if page_obj.number > 1 else None),
        'prev_query': parsed.query(page=page_obj.previous_page_number())
        if page_obj.has_previous()
        else '',
        'next_query': parsed.query(page=page_obj.next_page_number()) if page_obj.has_next() else '',
        'next_url': _with_query(
            reverse('short_url:dashboard'),
            parsed.query(page=page_obj.number if page_obj.number > 1 else None),
        ),
    }


@login_required
@require_GET
@ensure_csrf_cookie
def dashboard(request: AuthenticatedHttpRequest) -> HttpResponse:
    parsed = parse_filters(request)
    context = _list_context(request, parsed)
    context.update({'tags': owner_tags(request), 'form_values': parsed.params})
    return render_page(request, 'short_url/dashboard.html', context)


@login_required
@require_GET
def link_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The list partial. Opened directly (not by htmx) it goes to the full page instead."""
    parsed = parse_filters(request)
    if not is_htmx(request):
        query = parsed.query(page=request.GET.get('page'))
        return redirect(_with_query(reverse('short_url:dashboard'), query))

    context = _list_context(request, parsed)
    context['oob'] = True
    response = render(request, 'short_url/partials/link_list.html', context)
    response['HX-Push-Url'] = _with_query(reverse('short_url:dashboard'), context['page_query'])
    return response
