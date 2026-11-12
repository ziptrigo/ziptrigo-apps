"""Search over task titles, descriptions and comments (issue #181).

Search runs over the normalised plain-text copies stored next to the Markdown (`Task.title_text`,
`Task.description_text`, `Comment.body_text`; `apps.core.markdown.normalize_for_search`: accents
stripped, case folded, punctuation turned into spaces, formatting gone), so a query is normalised
the same way and matched against text that is already lower-case. Two dialects, plus the task number:

- **glob** (the default): `*` any text, `?` one character, everything else literal. The pattern is
  *unanchored*, so a query with no wildcard is a substring match (`rent` finds `parent`).
- **regex**: the query as written, either with `mode='regex'` or wrapped in slashes (`/pay.*rent/`).
  The dialect is what Python's `re` and PostgreSQL's engine share: no backreferences, no
  lookarounds. The DB runs it (`__regex`: `~` on PostgreSQL, a Python `REGEXP` function on SQLite),
  never `__iregex`: the text is already lower-case. The pattern is always a query parameter.
- **task number**: a glob query that is only a task number (`1000`, `#1000`) also finds the task
  with that exact number (`PreparedQuery.number`), next to everything whose text contains it.

On PostgreSQL the query runs under a 1 second `statement_timeout` (`search_guard`), the guard
against a pattern that is too expensive (nested quantifiers are not detected statically).

The snippets are the other place a user's pattern is run, and PostgreSQL's engine does not protect
Python's: its DFA answers `a.*a.*a.*a.*c` or `(a|aa)+$` at once, while a backtracking engine takes
minutes on the same text. So they are computed with the third-party `regex` package, whose
`search(timeout=...)` is bounded (`attach_matches`): a time budget for the whole page
(`SNIPPET_TIME_BUDGET`), only the field the database said matched, and at most `SNIPPET_SCAN_MAX`
characters of it. A row past the budget is still listed, without a snippet.

The query language, the PostgreSQL timeout guard and the snippet helpers are model-agnostic and
live in `apps.core.search` (see its docstring); what names `Task` or `Comment` stays here.

Every refusal is a `django.core.exceptions.ValidationError` with a message for the user.
"""

import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Protocol

from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.db.models import (
    BooleanField,
    Case,
    Exists,
    ExpressionWrapper,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Substr
from django.utils.safestring import SafeString

from apps.accounts.models import User

# The model-agnostic machinery lives in `apps.core.search` (shared with Notes, issue #260); it is
# re-exported here so every To-do caller and test keeps importing it from this module. Patching in
# a test: `connection` (the PostgreSQL branch of `search_guard`) is read by `apps.core.search`, so
# patch `apps.core.search.connection`; the constants and helpers `attach_matches` and the rest of
# this module use (`SNIPPET_TIME_BUDGET`, `snippet_regex`, ...) are this module's own names, so
# patch them here -- patching them on `apps.core.search` has no effect on To-do.
from apps.core.search import (
    EMPTY_MESSAGE,
    INVALID_MESSAGE,
    MAX_QUERY_CHARS,
    MODES,
    NUMBER_QUERY,
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

from ..models import Comment, Task
from .dates import SORT_DEFAULT, order_tasks

__all__ = [
    'EMPTY_MESSAGE',
    'INVALID_MESSAGE',
    'MAX_QUERY_CHARS',
    'MODES',
    'NUMBER_QUERY',
    'PAGE_SIZE',
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
    'search_condition',
    'search_guard',
    'search_queryset',
    'search_tasks',
    'snippet_parts',
    'snippet_regex',
    'to_postgres',
]

PAGE_SIZE = 50


class Narrowing(Protocol):
    """What `search_tasks` needs of the structured filters (`filters.FilterParams`)."""

    @property
    def sort(self) -> str: ...

    def narrow(self, queryset: QuerySet, user: User) -> QuerySet: ...


def search_condition(pattern: str, number: int | None = None) -> Q:
    """The condition "title, description or a non-deleted comment matches `pattern`" (the
    database's dialect: `PreparedQuery.db_pattern`), or the task's number is `number`, on a `Task`.
    A condition, not a join: a join would repeat a task once per matching comment and multiply the
    subtask counts a list annotates."""
    comment_match = Exists(
        Comment.objects.filter(
            task=OuterRef('pk'), deleted_at__isnull=True, body_text__regex=pattern
        )
    )
    condition = Q(title_text__regex=pattern) | Q(description_text__regex=pattern) | Q(comment_match)
    if number is not None:
        condition |= Q(number=number)
    return condition


def search_queryset(
    user: User, *, q: str, mode: str = 'glob', include_completed: bool = False
) -> QuerySet:
    """The user's non-trashed tasks (open ones only unless `include_completed`) whose title,
    description or a non-deleted comment matches `q`; unordered. Validates `q` now; the query
    itself runs when evaluated -- inside `search_guard()`."""
    query = prepare_query(q, mode)
    queryset = Task.objects.filter(user=user, deleted_at__isnull=True)
    if not include_completed:
        queryset = queryset.filter(completed_at__isnull=True)
    return queryset.filter(search_condition(query.db_pattern, query.number))


def order_results(
    queryset: QuerySet, sort: str = SORT_DEFAULT, direction: str | None = None
) -> QuerySet:
    """`queryset` in the foundation's `sort` order (`direction`: see `dates.order_tasks`), completed
    tasks after open ones."""
    ordered = order_tasks(queryset, sort, direction)
    open_first = Case(
        When(completed_at__isnull=False, then=Value(1)),
        default=Value(0),
        output_field=IntegerField(),
    )
    if not ordered.query.standard_ordering:
        # A reversed query flips every term when it is compiled, the open-first one too: ask for
        # it descending so it comes out ascending, and only the rest runs backwards.
        return ordered.order_by(open_first.desc(), *ordered.query.order_by)
    return ordered.order_by(open_first, *ordered.query.order_by)


def _matched_texts(
    tasks: list[Task], db_pattern: str
) -> dict[object, list[tuple[str, str]]] | None:
    """What the database says matched, per task id: `[(field, text)]` in the order the snippet
    looks (title, description, then the first non-deleted comment), each text cut to
    `SNIPPET_SCAN_MAX`. Two queries, both for the page's rows only and both under the search
    guard: one flags the title and the description; one then fetches the first matching comment's
    start for the tasks that matched in neither (only `body_text`, never every comment). `None`
    when the database refused (a timeout): the page then has no snippets."""
    by_id = {task.pk: task for task in tasks}
    matched: dict[object, list[tuple[str, str]]] = {task.pk: [] for task in tasks}
    flag = BooleanField()
    try:
        with search_guard():
            flagged = (
                Task.objects.filter(pk__in=list(by_id))
                .annotate(
                    in_title=ExpressionWrapper(Q(title_text__regex=db_pattern), output_field=flag),
                    in_description=ExpressionWrapper(
                        Q(description_text__regex=db_pattern), output_field=flag
                    ),
                )
                .values_list('pk', 'in_title', 'in_description')
            )
            for pk, in_title, in_description in flagged:
                task = by_id[pk]
                if in_title:
                    matched[pk].append(('title', task.title_text[:SNIPPET_SCAN_MAX]))
                if in_description:
                    matched[pk].append(('description', task.description_text[:SNIPPET_SCAN_MAX]))
        rest = [pk for pk, fields in matched.items() if not fields]
        if rest:
            first_comment = (
                Comment.objects.filter(
                    task=OuterRef('pk'), deleted_at__isnull=True, body_text__regex=db_pattern
                )
                .order_by('created_at', 'pk')
                .annotate(start=Substr('body_text', 1, SNIPPET_SCAN_MAX))
                .values('start')[:1]
            )
            with search_guard():
                commented = (
                    Task.objects.filter(pk__in=rest)
                    .annotate(comment_start=Subquery(first_comment))
                    .values_list('pk', 'comment_start')
                )
                for pk, comment_start in commented:
                    if comment_start:
                        matched[pk].append(('comment', comment_start))
    except ValidationError:
        return None
    return matched


def attach_matches(tasks: list[Task], query: PreparedQuery | None) -> None:
    """Set `search_field`, `search_snippet` (HTML) and `search_parts` (the same snippet as plain
    text, `(before, hit, after)`, for the API) on each task: where `query` matches (title, then
    description, then the first non-deleted comment that does), computed in Python on the
    normalised text. Both are empty without a query, for a row the budget didn't reach, and when
    Python finds nothing (the engines differ at the edges).

    Bounded, because the database's engine doesn't protect Python's (module docstring): the
    snippets use the `regex` package's timeout, the whole page shares `SNIPPET_TIME_BUDGET`
    seconds (each search gets what is left), only fields the database reported as matching are
    read, and at most `SNIPPET_SCAN_MAX` characters of each. The first timeout, or a spent budget,
    ends the snippets: the remaining rows stay in the results without one."""
    for task in tasks:
        task.search_field = ''
        task.search_snippet = SafeString('')
        task.search_parts = None
    if query is None or not tasks:
        return
    compiled = snippet_regex(query.pattern)
    if compiled is None:
        return
    deadline = time.monotonic() + SNIPPET_TIME_BUDGET
    matched = _matched_texts(tasks, query.db_pattern)
    if matched is None:
        return
    for task in tasks:
        for field, text in matched.get(task.pk, []):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                match = compiled.search(text, timeout=remaining)
            except TimeoutError:
                return
            if match is not None:
                task.search_field = field
                task.search_snippet = make_snippet(text, match)
                task.search_parts = snippet_parts(text, match)
                break


def paginate(
    queryset: QuerySet,
    *,
    query: PreparedQuery | None,
    page: int | str | None = 1,
    decorate: Callable[[QuerySet], QuerySet] | None = None,
) -> Page:
    """One page (`PAGE_SIZE`) of the ordered `queryset`, with the matches attached. With `query`
    the work runs under `search_guard()`. `decorate` (the lists' row loader: counts, labels,
    parent) is applied to the page's tasks only, keeping the page's order."""
    guard = search_guard() if query is not None else nullcontext()
    with guard:
        page_obj = Paginator(queryset, PAGE_SIZE).get_page(page)
        tasks = list(page_obj.object_list)
    if decorate is not None and tasks:
        loaded = {
            task.pk: task for task in decorate(Task.objects.filter(pk__in=[t.pk for t in tasks]))
        }
        tasks = [loaded[task.pk] for task in tasks if task.pk in loaded]
    attach_matches(tasks, query)
    page_obj.object_list = tasks
    return page_obj


def search_tasks(
    user: User,
    *,
    q: str,
    mode: str = 'glob',
    include_completed: bool = False,
    filters: Narrowing | None = None,
    page: int | str | None = 1,
    decorate: Callable[[QuerySet], QuerySet] | None = None,
) -> Page:
    """A page of the user's tasks matching `q`, narrowed by the structured `filters` when given
    (their own `q`, `mode` and `include_completed` are ignored: the arguments win) and ordered by
    the filters' sort (the default order otherwise), completed tasks last. Each task carries
    `search_field` and `search_snippet`. Raises `ValidationError`."""
    query = prepare_query(q, mode)
    queryset = search_queryset(user, q=q, mode=mode, include_completed=include_completed)
    sort = SORT_DEFAULT
    if filters is not None:
        queryset = filters.narrow(queryset, user)
        sort = filters.sort
    return paginate(order_results(queryset, sort), query=query, page=page, decorate=decorate)
