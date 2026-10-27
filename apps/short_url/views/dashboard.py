"""The dashboard (`/links/`): status tabs with counts, search, a tag filter, sorting and the list.

The page and `link_list` (its htmx partial) are built from the same context. A filter or tab change
re-fetches the partial into `#link-list` and the response's `HX-Push-Url` puts the *dashboard* URL
(not the partial's) in the address bar, so a reload or a shared link renders the full page.
"""

import re
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, QuerySet, TextField
from django.db.models.functions import Cast
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import is_htmx

from ..forms import PAGE_SIZE, STATUS_TABS, ParsedFilters, parse_filters
from ..models import LinkStatus, ShortLink
from ..services import enable_costs
from .common import render_page

#: What can be part of a tag (`services.normalize_tags`), case-insensitively for a search.
_TAG_QUERY = re.compile(r'[a-zA-Z0-9_-]+')

#: How many links `owner_tags` reads at most (the newest first), so the tag filter's cost stays
#: bounded however many links a user has. The hard cap on links makes this a ceiling, not a
#: usual limit.
TAG_SCAN_LIMIT = 1000


def _with_query(url: str, query: str) -> str:
    return f'{url}?{query}' if query else url


def state_filter(state: str, now: datetime) -> Q:
    """The rows in a dashboard state, matching `services.display_state` (so the tabs and the
    pills agree): a link that is `active` in the database may still be expired or past its cap."""
    if state in ('disabled', 'taken_down'):
        return Q(status=state)
    live = Q(status=LinkStatus.ACTIVE, held_for_review_at__isnull=True)
    not_expired = Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    capped = Q(max_clicks__isnull=False, click_count__gte=F('max_clicks'))
    match state:
        case 'expired':
            return live & Q(expires_at__lte=now)
        case 'capped':
            return live & not_expired & capped
        case 'active':
            return live & not_expired & ~capped
    raise ValueError(f'Unknown state: {state}')


def _visible_links(request: AuthenticatedHttpRequest) -> QuerySet:
    return ShortLink.objects.filter(owner=request.user).exclude(status=LinkStatus.DELETED)


def _filtered_links(
    request: AuthenticatedHttpRequest, parsed: ParsedFilters, now: datetime
) -> QuerySet:
    queryset = _visible_links(request).annotate(tags_text=Cast('tags', TextField()))
    if parsed.tab != 'all':
        queryset = queryset.filter(state_filter(parsed.tab, now))
    if parsed.tag:
        # Tags are ASCII `[a-z0-9_-]` (`services.normalize_tags`), so the JSON text of the list
        # holds each one as `"tag"` on both PostgreSQL and SQLite (a `contains` lookup on the
        # JSON field itself is not available on SQLite).
        queryset = queryset.filter(tags_text__contains=f'"{parsed.tag}"')
    if parsed.q:
        match = (
            Q(code_key__icontains=parsed.q)
            | Q(destination__icontains=parsed.q)
            | Q(title__icontains=parsed.q)
        )
        # The tags are searched as the JSON text of the list, which also holds `["`, `",` and `]`:
        # only a query that could be (part of) a tag may look there, or `"` would match every
        # tagged link. Tags are stored lower-cased.
        if _TAG_QUERY.fullmatch(parsed.q):
            match |= Q(tags_text__icontains=parsed.q.lower())
        queryset = queryset.filter(match)
    return queryset.order_by(parsed.sort, '-created_at')


def tab_counts(request: AuthenticatedHttpRequest, now: datetime) -> dict[str, int]:
    """The number in each status tab (the "All" tab counts every link that isn't deleted)."""
    aggregates = {'all': Count('pk')}
    for key, _label in STATUS_TABS:
        if key != 'all':
            aggregates[key] = Count('pk', filter=state_filter(key, now))
    return _visible_links(request).aggregate(**aggregates)


def owner_tags(request: AuthenticatedHttpRequest) -> list[str]:
    """The tags on the user's newest `TAG_SCAN_LIMIT` links, sorted, for the tag filter."""
    tags: set[str] = set()
    newest = _visible_links(request).order_by('-created_at').values_list('tags', flat=True)
    for value in newest[:TAG_SCAN_LIMIT]:
        tags.update(value or [])
    return sorted(tags)


def _list_context(request: AuthenticatedHttpRequest, parsed: ParsedFilters) -> dict:
    now = timezone.now()
    page_obj = Paginator(_filtered_links(request, parsed, now), PAGE_SIZE).get_page(
        request.GET.get('page')
    )
    links = list(page_obj.object_list)
    # What enabling each disabled row costs, so its menu can ask before charging.
    costs = enable_costs(request.user, links)
    for link in links:
        link.enable_cost = costs[link.pk]

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
