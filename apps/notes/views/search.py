"""`/notes/search/`: search over note titles and content, with the structured filters.

The page holds the form (search text, the regex switch, the filter bar) and `#search-results`;
every change re-fetches `/notes/search/list/` into it, and the response's `HX-Push-Url` puts the
canonical page URL in the address bar (To-do's and Receipts' pattern), so a reload or a shared link
renders the full page. A saved filter's page (`views.filters.filter_page`) is this same page with
the filter's params applied. The rules are `services.search` / `services.filters`; a refusal (an
empty, too long or invalid pattern, a search that took too long) is a 422 line in `#search-msg`.
"""

from dataclasses import dataclass
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

from ..models import SavedFilter, Tag
from ..services import (
    DEFAULT_DIRECTIONS,
    SORTS,
    FilterParams,
    describe_params,
    filter_page,
    quote_create,
)
from ..services.filters import QUERY_KEYS, UPDATED_RANGES
from ..services.saved_filters import dropped_tag_count, stored_params
from ..services.search import MAX_QUERY_CHARS, SCOPES
from .common import limited, message_response, render_page, validation_lines
from .lists import decorate, excerpt

SEARCH_MSG = '#search-msg'
#: What the "Shared" select offers: any note, or only the shared / the not shared ones.
SHARED_CHOICES = (('', 'Any'), ('yes', 'Shared'), ('no', 'Not shared'))
#: What the sort direction select offers.
DIRECTION_CHOICES = (('asc', 'Ascending'), ('desc', 'Descending'))


def search_url() -> str:
    return reverse('notes:search')


def page_url_for(saved: SavedFilter | None) -> str:
    """The page URL: the search page, or a saved filter's."""
    return reverse('notes:filter', args=[saved.pk]) if saved is not None else search_url()


@dataclass(frozen=True, slots=True)
class UserTags:
    """The requester's tags, read once per request and passed down: the filter bar's checkboxes,
    the ids that validate a query's `tags` and the names a summary prints."""

    tags: list[Tag]
    ids: set[str]
    names: dict[str, str]

    @classmethod
    def load(cls, user) -> 'UserTags':
        tags = list(Tag.objects.filter(user=user))
        return cls(
            tags=tags,
            ids={str(tag.pk) for tag in tags},
            names={str(tag.pk): tag.name for tag in tags},
        )


@dataclass(frozen=True, slots=True)
class SearchState:
    """What one search request works with, computed once: the user's tags, the filter as saved
    (`stored`, read with those tags) and the params the request asks for."""

    saved: SavedFilter | None
    user_tags: UserTags
    stored: FilterParams | None
    params: FilterParams

    @property
    def runs(self) -> bool:
        """Whether the page runs a query: when `params` select anything, and also on a saved filter
        shown as saved (one whose tag was deleted selects "every note in its scope", as its count
        says)."""
        return self.params.has_criteria or (self.stored is not None and self.params == self.stored)


def search_state(
    request: AuthenticatedHttpRequest, saved: SavedFilter | None, *, stored_default: bool
) -> SearchState:
    """The state of a search request. With `stored_default` (a saved filter's page), a request
    without any of the filter keys in its query string asks for the filter as saved."""
    user_tags = UserTags.load(request.user)
    stored = stored_params(saved, valid_tag_ids=user_tags.ids) if saved is not None else None
    if stored is not None and stored_default and not any(key in request.GET for key in QUERY_KEYS):
        params = stored
    else:
        params = FilterParams.from_query(request.GET, valid_tag_ids=user_tags.ids)
    return SearchState(saved=saved, user_tags=user_tags, stored=stored, params=params)


def _page_query(params: FilterParams, saved: SavedFilter | None, page: int = 1) -> str:
    """The query string of the page URL for `params`: the canonical one, with the sort written
    out on a saved filter's page (so that "nothing selected" isn't read as "the filter as saved")
    and `page` after page 1."""
    return params.to_query(keep_sort=saved is not None, page=page if page > 1 else None)


def build_context(request: AuthenticatedHttpRequest, state: SearchState) -> dict:
    """Everything the page and the results partial need. The search runs here when the params select
    anything; a refusal is `error_lines`, with no results. Charge the rate limit before calling."""
    user = request.user
    params, saved, stored = state.params, state.saved, state.stored
    context: dict = {
        'view': 'search',
        'params': params,
        'saved': saved,
        'current_filter_id': saved.pk if saved is not None else None,
        'heading': saved.name if saved is not None else 'Search',
        'tags': state.user_tags.tags,
        'selected_tags': set(params.tags),
        'scopes': SCOPES,
        'shared_choices': SHARED_CHOICES,
        'updated_ranges': UPDATED_RANGES,
        'sorts': SORTS,
        'directions': DIRECTION_CHOICES,
        'default_directions': DEFAULT_DIRECTIONS,
        'max_query_chars': MAX_QUERY_CHARS,
        'today': user_today(user),
        'page_url': page_url_for(saved),
        'list_url': reverse('notes:search-list'),
        'hidden_pairs': params.to_pairs(keep_sort=True),
        'ran': False,
        'error_lines': [],
        'show_sidebar_search': False,
        'differs': stored is not None and params != stored,
        'filter_summary': '',
        'dropped_tags': 0,
        # What a result row's menu needs.
        'duplicate_cost': quote_create(user),
    }
    if saved is not None and stored is not None:
        context['filter_summary'] = describe_params(stored, state.user_tags.names)
        context['dropped_tags'] = dropped_tag_count(saved, user, stored=stored)
    if not state.runs:
        return context
    try:
        page_obj = filter_page(user, params, page=request.GET.get('page'), decorate=decorate)
    except ValidationError as exc:
        context['error_lines'] = validation_lines(exc)
        return context
    notes = page_obj.object_list
    for note in notes:
        note.excerpt = excerpt(note)
    number = page_obj.number
    page_query = _page_query(params, saved, number)
    context.update(
        {
            'ran': True,
            'page_obj': page_obj,
            'notes': notes,
            'count': page_obj.paginator.count,
            'page_query': page_query,
            'prev_query': _page_query(params, saved, number - 1) if page_obj.has_previous() else '',
            'next_query': _page_query(params, saved, number + 1) if page_obj.has_next() else '',
            # What the pager's htmx requests add so the partial knows which filter asks.
            'filter_suffix': f'&filter={saved.pk}' if saved is not None else '',
            'is_search': bool(params.q.strip()),
            # Where a row's action (archive, move to trash) comes back to: this search.
            'list_next': f'{context["page_url"]}?{page_query}'
            if page_query
            else context['page_url'],
        }
    )
    return context


def render_search_page(
    request: AuthenticatedHttpRequest, saved: SavedFilter | None
) -> HttpResponse:
    """The full page for the request's params (a saved filter's page without filter keys shows the
    filter as saved); rate limited when it runs a search."""
    state = search_state(request, saved, stored_default=True)
    if state.runs:
        if (response := limited(request, 'NOTES_SEARCH_USER', retarget=SEARCH_MSG)) is not None:
            return response
    context = build_context(request, state)
    nav = f'filter-{saved.pk}' if saved is not None else 'search'
    return render_page(
        request,
        'notes/search.html',
        context,
        nav=nav,
        status=422 if context['error_lines'] else 200,
    )


@login_required
@require_GET
@ensure_csrf_cookie
def search_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return render_search_page(request, None)


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
    state = search_state(request, saved, stored_default=False)
    params = state.params
    page_url = page_url_for(saved)
    if not is_htmx(request):
        query = _page_query(params, saved, _page_number(request))
        return redirect(f'{page_url}?{query}' if query else page_url)

    if state.runs:
        if (response := limited(request, 'NOTES_SEARCH_USER', retarget=SEARCH_MSG)) is not None:
            return response
    context = build_context(request, state)
    if context['error_lines']:
        return message_response(request, context['error_lines'], retarget=SEARCH_MSG)
    context['clear_message'] = True
    response = render(request, 'notes/partials/search_results.html', context)
    query = context.get('page_query', _page_query(params, saved))
    response['HX-Push-Url'] = f'{page_url}?{query}' if query else page_url
    return response


def _page_number(request: AuthenticatedHttpRequest) -> int:
    try:
        return max(int(request.GET.get('page', '1')), 1)
    except ValueError:
        return 1
