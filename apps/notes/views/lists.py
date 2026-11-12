"""The note lists and the pages that hold them: Notes, Archived and Trash.

Each page and `/notes/list/` (the partial every one of them fetches with htmx) are built from the
same context (`list_context`), To-do's and Receipts' pattern: a change re-fetches the partial into
`#note-list` and the response's `HX-Push-Url` puts the *page* URL in the address bar, so a reload or
a shared link renders the full page.
"""

from datetime import timedelta
from math import ceil
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import is_htmx
from apps.core.markdown import to_plain_text

from ..limits import EXCERPT_CHARS, PAGE_SIZE
from ..models import Note, NotesSettings, Tag
from ..services import (
    DEFAULT_DIRECTIONS,
    DIRECTIONS,
    SORTS,
    active_notes,
    archived_notes,
    free_slot_open,
    get_account,
    links,
    order_notes,
    quote_create,
    restore_cost_for,
    sort_direction,
    sort_for,
    trashed_notes,
)
from ..services.lists import VIEW_DEFAULT_SORT
from .common import render_page

VIEWS = ('notes', 'archived', 'trash')
TITLES = {'notes': 'Notes', 'archived': 'Archived', 'trash': 'Trash'}
URL_NAMES = {'notes': 'notes:list', 'archived': 'notes:archived', 'trash': 'notes:trash'}
#: How much of a note's text is turned into plain text for its row's excerpt: the row shows 140
#: characters, so parsing a 50,000-character note in full for each of 50 rows would be wasted work.
EXCERPT_SOURCE_CHARS = 1500


def page_url(view: str) -> str:
    return reverse(URL_NAMES[view])


def excerpt(note: Note) -> str:
    """The first characters of the note's text without Markdown marks, on one line."""
    try:
        text = to_plain_text(note.content[:EXCERPT_SOURCE_CHARS], profile='extended')
    except ValueError:
        text = note.content[:EXCERPT_SOURCE_CHARS]
    text = ' '.join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1].rstrip() + '…'


def _uuid_or_empty(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        return ''


def _query(**params: str) -> str:
    return '&'.join(f'{key}={value}' for key, value in params.items() if value)


def sort_links(view: str, sort: str, direction: str, tag: str) -> list[dict]:
    """What the "Sort by" bar renders, one entry per sort the view offers: its `key`, `label`, the
    `query` of its link (without the `?`; empty for the plain default) and, for the active one, its
    `direction`. A sort's link asks for its own default direction, except the active one, which
    asks for the opposite: a second click reverses it."""
    default_sort = VIEW_DEFAULT_SORT.get(view, 'updated')
    links = []
    for key, label in SORTS.items():
        if key == 'manual' and view != 'notes':
            continue
        active = key == sort
        wanted = ('asc' if direction == 'desc' else 'desc') if active else DEFAULT_DIRECTIONS[key]
        query = _query(
            sort=key if key != default_sort else '',
            dir=wanted if wanted != DEFAULT_DIRECTIONS[key] else '',
            tag=tag,
        )
        links.append(
            {
                'key': key,
                'label': label,
                'query': query,
                'active': active,
                'direction': direction if active else '',
            }
        )
    return links


def decorate(queryset: QuerySet) -> QuerySet:
    """What a list's rows need loaded: the share flag (one subquery) and the tags (one query for
    the page). Search applies it to its page of results too."""
    return links.with_active_link_flag(queryset).prefetch_related('tags')


def _queryset(view: str, user) -> QuerySet:
    return {'notes': active_notes, 'archived': archived_notes, 'trash': trashed_notes}[view](user)


def list_context(request: AuthenticatedHttpRequest, view: str) -> dict:
    """The context of the list `view` for the requester, reading `sort`, `dir`, `tag` and `page`
    from the query string."""
    user = request.user
    sort = sort_for(view, request.GET.get('sort'))
    direction = sort_direction(sort, request.GET.get('dir'))
    tags = list(Tag.objects.filter(user=user))
    tag = next(
        (candidate for candidate in tags if str(candidate.pk) == request.GET.get('tag')), None
    )
    queryset = _queryset(view, user)
    if tag is not None:
        queryset = queryset.filter(tags=tag)
    if view == 'trash':
        queryset = queryset.order_by('-deleted_at', 'pk')
    else:
        queryset = order_notes(queryset, sort, direction)
    queryset = decorate(queryset)
    page_obj = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))
    notes = list(page_obj.object_list)
    for note in notes:
        note.excerpt = excerpt(note)
    context: dict = {
        'view': view,
        'list_title': TITLES[view],
        'notes': notes,
        'page_obj': page_obj,
        'sort': sort,
        'tags': tags,
        'tag': tag,
        'list_url': reverse('notes:list-partial'),
        'page_url': page_url(view),
        'reorder_url': reverse('notes:reorder'),
        # Drag handles only where a manual order is what the list shows.
        'sortable': view == 'notes' and sort == 'manual' and direction == 'asc' and tag is None,
        'duplicate_cost': quote_create(user),
    }
    if view == 'trash':
        now = timezone.now()
        # Settings and the free-slot count once for the whole page, not once per row.
        config = NotesSettings.load()
        trash_days = config.trash_days
        slot_open = free_slot_open(user, config)
        for note in notes:
            assert note.deleted_at is not None  # filtered above
            purge_at = note.deleted_at + timedelta(days=trash_days)
            note.days_left = max(ceil((purge_at - now).total_seconds() / 86400), 0)
            note.restore_cost = restore_cost_for(note, config, slot_open)
        context['trash_days'] = trash_days
    tag_id = str(tag.pk) if tag is not None else ''
    # `dir` is only written when it is not the sort's own default, so the plain URLs stay as they were.
    base = {
        'sort': sort if sort != VIEW_DEFAULT_SORT.get(view, 'updated') else '',
        'dir': direction if direction != DEFAULT_DIRECTIONS[sort] else '',
        'tag': tag_id,
    }
    for candidate in tags:
        candidate.list_query = _query(**{**base, 'tag': str(candidate.pk)})
    context.update(
        {
            'sort_links': sort_links(view, sort, direction, tag_id),
            'all_tags_query': _query(**{**base, 'tag': ''}),
            'page_query': _query(**base, page=str(page_obj.number) if page_obj.number > 1 else ''),
            'prev_query': _query(**base, page=str(page_obj.previous_page_number()))
            if page_obj.has_previous()
            else '',
            'next_query': _query(**base, page=str(page_obj.next_page_number()))
            if page_obj.has_next()
            else '',
        }
    )
    # Where a row's action comes back to: this list, on this page, in this order.
    context['list_next'] = (
        f'{context["page_url"]}?{context["page_query"]}'
        if context['page_query']
        else context['page_url']
    )
    return context


def _page(request: AuthenticatedHttpRequest, view: str) -> HttpResponse:
    context = list_context(request, view)
    return render_page(
        request, 'notes/list.html', context, nav='notes' if view == 'notes' else view
    )


@login_required
@require_GET
@ensure_csrf_cookie
def notes_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _page(request, 'notes')


@login_required
@require_GET
@ensure_csrf_cookie
def archived_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _page(request, 'archived')


@login_required
@require_GET
@ensure_csrf_cookie
def trash_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    return _page(request, 'trash')


@login_required
@require_GET
def note_list(request: AuthenticatedHttpRequest) -> HttpResponse:
    """The list partial (`view`, `sort`, `dir`, `tag`, `page`). Opened directly (not by htmx) it
    goes to the full page instead."""
    view = request.GET.get('view', '')
    if view not in VIEWS:
        view = 'notes'
    if not is_htmx(request):
        sort = request.GET.get('sort', '')
        direction = request.GET.get('dir', '')
        page = request.GET.get('page', '')
        # The same rule as the list itself and the search: a direction is written only when it isn't
        # the sort's default.
        effective = sort_for(view, sort)
        query = _query(
            sort=sort if sort in SORTS else '',
            dir=direction
            if direction in DIRECTIONS and direction != DEFAULT_DIRECTIONS[effective]
            else '',
            tag=_uuid_or_empty(request.GET.get('tag', '')),
            page=str(int(page)) if page.isascii() and page.isdigit() else '',
        )
        return redirect(f'{page_url(view)}?{query}' if query else page_url(view))

    get_account(request.user)  # the partial is the first thing a stale tab may fetch
    context = list_context(request, view)
    response = render(request, 'notes/partials/note_list.html', context)
    query = context.get('page_query', '')
    response['HX-Push-Url'] = f'{page_url(view)}?{query}' if query else page_url(view)
    return response
