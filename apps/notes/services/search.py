"""Search over note titles and content (issue #260, PR 5; To-do's search, #181, for notes).

Search runs over the normalised plain-text copies stored next to the text (`Note.title_text`,
`Note.content_text`; `apps.core.markdown.normalize_for_search`: accents stripped, case folded,
punctuation turned into spaces, Markdown formatting gone), so a query is normalised the same way
and matched against text that is already lower-case. Two dialects, `glob` (the default) and
`regex`, exactly as in To-do (see `apps.core.search`'s docstring for the language, the PostgreSQL
timeout guard and the snippet bounds, which are shared and not repeated here).

What differs from To-do: only the title and the content are searched (no comments, no version
history: a `NoteVersion` has no search column and is never searched), there is no number
shortcut (`PreparedQuery.number` is ignored), and the notes searched are the user's **outside the
Trash**, narrowed by `scope`: `active` (not archived, the default), `archived` or `all`. A note in
the Trash is never found, whatever the scope.

The snippets are computed as To-do's are, bounded: the third-party `regex` package's timeout, one
time budget for the page (`SNIPPET_TIME_BUDGET`), only the field the database said matched (the
title, else the content) and at most `SNIPPET_SCAN_MAX` characters of it. A row past the budget is
still listed, without a snippet.

Every refusal is a `django.core.exceptions.ValidationError` with a message for the user.
"""

import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Protocol

from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.db.models import BooleanField, ExpressionWrapper, Q, QuerySet
from django.utils.safestring import SafeString

from apps.accounts.models import User

# The model-agnostic machinery lives in `apps.core.search` (shared with To-do); it is re-exported
# here so Notes' callers and tests import it from this module. Patching in a test: `connection`
# (the PostgreSQL branch of `search_guard`) is read by `apps.core.search`, so patch
# `apps.core.search.connection`; the names `attach_matches` and the rest of this module use
# (`SNIPPET_TIME_BUDGET`, `snippet_regex`, `time`) are this module's own, so patch them here.
from apps.core.search import (
    EMPTY_MESSAGE,
    INVALID_MESSAGE,
    MAX_QUERY_CHARS,
    MODES,
    SNIPPET_CONTEXT,
    SNIPPET_MARK_MAX,
    SNIPPET_SCAN_MAX,
    SNIPPET_TIME_BUDGET,
    STATEMENT_TIMEOUT_MS,
    TIMEOUT_MESSAGE,
    TOO_LONG_MESSAGE,
    BoundedPattern,
    PreparedQuery,
    SpanMatch,
    glob_to_regex,
    lower_literals,
    make_snippet,
    normalize_glob,
    prepare_query,
    search_guard,
    snippet_parts,
    snippet_regex,
    to_postgres,
)

from ..limits import PAGE_SIZE
from ..models import Note
from .lists import order_notes

__all__ = [
    'EMPTY_MESSAGE',
    'INVALID_MESSAGE',
    'MAX_QUERY_CHARS',
    'MODES',
    'PAGE_SIZE',
    'SCOPES',
    'SCOPE_DEFAULT',
    'SNIPPET_CONTEXT',
    'SNIPPET_MARK_MAX',
    'SNIPPET_SCAN_MAX',
    'SNIPPET_TIME_BUDGET',
    'STATEMENT_TIMEOUT_MS',
    'TIMEOUT_MESSAGE',
    'TOO_LONG_MESSAGE',
    'BoundedPattern',
    'Narrowing',
    'PreparedQuery',
    'SpanMatch',
    'attach_matches',
    'glob_to_regex',
    'lower_literals',
    'make_snippet',
    'normalize_glob',
    'order_results',
    'paginate',
    'prepare_query',
    'scope_condition',
    'scoped_notes',
    'search_condition',
    'search_guard',
    'search_notes',
    'search_queryset',
    'snippet_parts',
    'snippet_regex',
    'to_postgres',
]

#: Which of the user's notes (outside the Trash) a search looks at, with the names the filter bar
#: shows. The Trash is never searched.
SCOPES = {'active': 'Notes', 'archived': 'Archived', 'all': 'Notes and archived'}
SCOPE_DEFAULT = 'active'


class Narrowing(Protocol):
    """What `search_notes` needs of the structured filters (`filters.FilterParams`)."""

    @property
    def sort(self) -> str: ...

    def narrow(self, queryset: QuerySet, user: User) -> QuerySet: ...


def scope_condition(scope: str) -> Q:
    """The condition on a `Note` that selects `scope` (empty for `all`, and for anything unknown,
    which is read as the default `active`)."""
    if scope == 'all':
        return Q()
    if scope == 'archived':
        return Q(archived_at__isnull=False)
    return Q(archived_at__isnull=True)


def scoped_notes(user: User, scope: str = SCOPE_DEFAULT) -> QuerySet:
    """The user's notes outside the Trash that `scope` selects; unordered."""
    return Note.objects.filter(scope_condition(scope), user=user, deleted_at__isnull=True)


def search_condition(pattern: str) -> Q:
    """The condition "the title or the content matches `pattern`" (the database's dialect:
    `PreparedQuery.db_pattern`) on a `Note`."""
    return Q(title_text__regex=pattern) | Q(content_text__regex=pattern)


def search_queryset(
    user: User, *, q: str, mode: str = 'glob', scope: str = SCOPE_DEFAULT
) -> QuerySet:
    """The user's notes in `scope` (never the Trash) whose title or content matches `q`;
    unordered. Validates `q` now; the query itself runs when evaluated -- inside
    `search_guard()`."""
    query = prepare_query(q, mode)
    return scoped_notes(user, scope).filter(search_condition(query.db_pattern))


def order_results(
    queryset: QuerySet, sort: str = 'updated', direction: str | None = None
) -> QuerySet:
    """`queryset` in `sort` order (`lists.order_notes`: `updated` newest first by default, `created`,
    `title` or `manual`; `direction` reverses it)."""
    return order_notes(queryset, sort, direction)


def _matched_texts(
    notes: list[Note], db_pattern: str
) -> dict[object, list[tuple[str, str]]] | None:
    """What the database says matched, per note id: `[(field, text)]` in the order the snippet looks
    (the title, then the content), each text cut to `SNIPPET_SCAN_MAX`. One query for the page's
    rows only, under the search guard, flagging both fields. `None` when the database refused (a
    timeout): the page then has no snippets."""
    by_id = {note.pk: note for note in notes}
    matched: dict[object, list[tuple[str, str]]] = {note.pk: [] for note in notes}
    flag = BooleanField()
    try:
        with search_guard():
            flagged = (
                Note.objects.filter(pk__in=list(by_id))
                .annotate(
                    in_title=ExpressionWrapper(Q(title_text__regex=db_pattern), output_field=flag),
                    in_content=ExpressionWrapper(
                        Q(content_text__regex=db_pattern), output_field=flag
                    ),
                )
                .values_list('pk', 'in_title', 'in_content')
            )
            for pk, in_title, in_content in flagged:
                note = by_id[pk]
                if in_title:
                    matched[pk].append(('title', note.title_text[:SNIPPET_SCAN_MAX]))
                if in_content:
                    matched[pk].append(('content', note.content_text[:SNIPPET_SCAN_MAX]))
    except ValidationError:
        return None
    return matched


def attach_matches(notes: list[Note], query: PreparedQuery | None) -> None:
    """Set `search_field` (`title` or `content`), `search_snippet` (HTML) and `search_parts` (the
    same snippet as plain text, `(before, hit, after)`, for the API) on each note: where `query`
    matches (the title, else the content), computed in Python on the normalised text. All are empty
    without a query, for a row the budget didn't reach, and when Python finds nothing (the engines
    differ at the edges).

    Bounded, because the database's engine doesn't protect Python's (module docstring): the
    snippets use the `regex` package's timeout, the whole page shares `SNIPPET_TIME_BUDGET`
    seconds (each search gets what is left), only fields the database reported as matching are
    read, and at most `SNIPPET_SCAN_MAX` characters of each. The first timeout, or a spent budget,
    ends the snippets: the remaining rows stay in the results without one."""
    for note in notes:
        note.search_field = ''
        note.search_snippet = SafeString('')
        note.search_parts = None
    if query is None or not notes:
        return
    compiled = snippet_regex(query.pattern)
    if compiled is None:
        return
    deadline = time.monotonic() + SNIPPET_TIME_BUDGET
    matched = _matched_texts(notes, query.db_pattern)
    if matched is None:
        return
    for note in notes:
        for field, text in matched.get(note.pk, []):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                match = compiled.search(text, timeout=remaining)
            except TimeoutError:
                return
            if match is not None:
                note.search_field = field
                note.search_snippet = make_snippet(text, match)
                note.search_parts = snippet_parts(text, match)
                break


def paginate(
    queryset: QuerySet,
    *,
    query: PreparedQuery | None,
    page: int | str | None = 1,
    decorate: Callable[[QuerySet], QuerySet] | None = None,
) -> Page:
    """One page (`PAGE_SIZE`) of the ordered `queryset`, with the matches attached. With `query`
    the work runs under `search_guard()`. `decorate` (the lists' row loader: tags, the share flag)
    is applied to the page's notes only, keeping the page's order."""
    guard = search_guard() if query is not None else nullcontext()
    with guard:
        page_obj = Paginator(queryset, PAGE_SIZE).get_page(page)
        notes = list(page_obj.object_list)
    if decorate is not None and notes:
        loaded = {
            note.pk: note for note in decorate(Note.objects.filter(pk__in=[n.pk for n in notes]))
        }
        notes = [loaded[note.pk] for note in notes if note.pk in loaded]
    attach_matches(notes, query)
    page_obj.object_list = notes
    return page_obj


def search_notes(
    user: User,
    *,
    q: str,
    mode: str = 'glob',
    scope: str = SCOPE_DEFAULT,
    filters: Narrowing | None = None,
    page: int | str | None = 1,
    decorate: Callable[[QuerySet], QuerySet] | None = None,
) -> Page:
    """A page of the user's notes matching `q` in `scope`, narrowed by the structured `filters`
    when given (their own `q`, `mode` and `scope` are ignored: the arguments win) and ordered by
    the filters' sort (newest updated first otherwise). Each note carries `search_field` and
    `search_snippet`. Raises `ValidationError`."""
    query = prepare_query(q, mode)
    queryset = search_queryset(user, q=q, mode=mode, scope=scope)
    sort = 'updated'
    if filters is not None:
        queryset = filters.narrow(queryset, user)
        sort = filters.sort
    return paginate(order_results(queryset, sort), query=query, page=page, decorate=decorate)
