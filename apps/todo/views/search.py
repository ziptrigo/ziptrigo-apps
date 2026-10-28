"""`/todo/search/`: search over titles, descriptions and comments, with the structured filters.

The page holds the form (search text, the two switches, the filter bar) and `#search-results`; every
change re-fetches `/todo/search/list/` into it, and the response's `HX-Push-Url` puts the canonical
page URL in the address bar (the Receipts dashboard's pattern), so a reload or a shared link
renders the full page. A saved filter's page (`views.filters.filter_page`) is this same page with
the filter's params applied. The rules are `services.search` / `services.filters`; a refusal (an
empty, too long or invalid pattern, a search that took too long) is a 422 line in `#search-msg`.
"""

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.services.timezones import user_today
from apps.core.htmx import is_htmx

from ..models import Label, SavedFilter
from ..services import SORTS, FilterParams, describe_params, filter_page, parse_filter_params
from ..services.filters import DATE_RANGES, QUERY_KEYS, label_names
from ..services.saved_filters import stored_params
from ..services.search import MAX_QUERY_CHARS
from .common import limited, message_response, render_page, validation_lines
from .lists import decorate

SEARCH_MSG = '#search-msg'
PRIORITY_CHOICES = ((1, 'Priority 1'), (2, 'Priority 2'), (3, 'Priority 3'), (0, 'No priority'))


def search_url() -> str:
    return reverse('todo:search')


def page_url_for(saved: SavedFilter | None) -> str:
    """The page URL: the search page, or a saved filter's."""
    return reverse('todo:filter', args=[saved.pk]) if saved is not None else search_url()


def requested_params(request: AuthenticatedHttpRequest, saved: SavedFilter | None) -> FilterParams:
    """The params the request asks for. A saved filter's page without any of the filter keys in
    its query string shows the filter as saved."""
    if saved is not None and not any(key in request.GET for key in QUERY_KEYS):
        return stored_params(saved, request.user)
    return parse_filter_params(request.GET, request.user)


def _page_query(params: FilterParams, saved: SavedFilter | None, page: int = 1) -> str:
    """The query string of the page URL for `params`: the canonical one, with the sort written
    out on a saved filter's page (so that "nothing selected" isn't read as "the filter as saved")
    and `page` after page 1."""
    return params.to_query(keep_sort=saved is not None, page=page if page > 1 else None)


def runs_search(user, params: FilterParams, saved: SavedFilter | None) -> bool:
    """Whether the page runs a query: when `params` select anything, and also on a saved filter
    shown as saved (one whose label was deleted selects "all open tasks", as its count says)."""
    return params.has_criteria or (saved is not None and params == stored_params(saved, user))


def build_context(
    request: AuthenticatedHttpRequest, params: FilterParams, saved: SavedFilter | None
) -> dict:
    """Everything the page and the results partial need. The search runs here when `params` select
    anything; a refusal is `error_lines`, with no results. Charge the rate limit before calling."""
    user = request.user
    labels = list(Label.objects.filter(user=user))
    context: dict = {
        'view': 'search',
        'params': params,
        'saved': saved,
        'current_filter_id': saved.pk if saved is not None else None,
        'heading': saved.name if saved is not None else 'Search',
        'labels': labels,
        'selected_labels': set(params.labels),
        'selected_priorities': set(params.priorities),
        'priority_choices': PRIORITY_CHOICES,
        'date_ranges': DATE_RANGES,
        'sorts': SORTS,
        'max_query_chars': MAX_QUERY_CHARS,
        'today': user_today(user),
        'page_url': page_url_for(saved),
        'list_url': reverse('todo:search-list'),
        'hidden_pairs': params.to_pairs(keep_sort=True),
        'ran': False,
        'error_lines': [],
        'show_sidebar_search': False,
        'differs': saved is not None and params != stored_params(saved, user),
        'filter_summary': '',
    }
    if saved is not None:
        context['filter_summary'] = describe_params(stored_params(saved, user), label_names(user))
    if not runs_search(user, params, saved):
        return context
    try:
        page_obj = filter_page(user, params, page=request.GET.get('page'), decorate=decorate)
    except ValidationError as exc:
        context['error_lines'] = validation_lines(exc)
        return context
    number = page_obj.number
    context.update(
        {
            'ran': True,
            'page_obj': page_obj,
            'tasks': page_obj.object_list,
            'count': page_obj.paginator.count,
            'page_query': _page_query(params, saved, number),
            'prev_query': _page_query(params, saved, number - 1) if page_obj.has_previous() else '',
            'next_query': _page_query(params, saved, number + 1) if page_obj.has_next() else '',
            # What the pager's htmx requests add so the partial knows which filter asks.
            'filter_suffix': f'&filter={saved.pk}' if saved is not None else '',
            'is_search': bool(params.q.strip()),
        }
    )
    return context


def render_search_page(
    request: AuthenticatedHttpRequest, params: FilterParams, saved: SavedFilter | None
) -> HttpResponse:
    """The full page for `params` (rate limited when it runs a search)."""
    if runs_search(request.user, params, saved):
        if (response := limited(request, 'TODO_SEARCH_USER', retarget=SEARCH_MSG)) is not None:
            return response
    context = build_context(request, params, saved)
    nav = f'filter-{saved.pk}' if saved is not None else 'search'
    return render_page(
        request,
        'todo/search.html',
        context,
        nav=nav,
        status=422 if context['error_lines'] else 200,
    )


@login_required
@require_GET
@ensure_csrf_cookie
def search_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return render_search_page(request, parse_filter_params(request.GET, request.user), None)


@login_required
@require_GET
def search_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The results partial (the filters of the query string, `page`, and `filter`: the saved
    filter whose page asks, so "Update filter" can compare). Opened directly (not by htmx) it goes
    to the full page instead."""
    saved: SavedFilter | None = None
    raw_filter = request.GET.get('filter', '')
    if raw_filter:
        try:
            saved = SavedFilter.objects.filter(pk=UUID(raw_filter), user=request.user).first()
        except ValueError:
            saved = None
    params = parse_filter_params(request.GET, request.user)
    page_url = page_url_for(saved)
    if not is_htmx(request):
        query = _page_query(params, saved, _page_number(request))
        return redirect(f'{page_url}?{query}' if query else page_url)

    if runs_search(request.user, params, saved):
        if (response := limited(request, 'TODO_SEARCH_USER', retarget=SEARCH_MSG)) is not None:
            return response
    context = build_context(request, params, saved)
    if context['error_lines']:
        return message_response(request, context['error_lines'], retarget=SEARCH_MSG)
    context['clear_message'] = True
    response = render(request, 'todo/partials/search_results.html', context)
    query = context.get('page_query', _page_query(params, saved))
    response['HX-Push-Url'] = f'{page_url}?{query}' if query else page_url
    return response


def _page_number(request: AuthenticatedHttpRequest) -> int:
    try:
        return max(int(request.GET.get('page', '1')), 1)
    except ValueError:
        return 1
