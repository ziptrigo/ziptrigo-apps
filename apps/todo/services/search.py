"""Search over task titles, descriptions and comments (issue #181).

Search runs over the normalised plain-text copies stored next to the Markdown (`Task.title_text`,
`Task.description_text`, `Comment.body_text`; `apps.core.markdown.normalize_for_search`: accents
stripped, case folded, punctuation turned into spaces, formatting gone), so a query is normalised
the same way and matched against text that is already lower-case. Two dialects:

- **glob** (the default): `*` any text, `?` one character, everything else literal. The pattern is
  *unanchored*, so a query with no wildcard is a substring match (`rent` finds `parent`).
- **regex**: the query as written, either with `mode='regex'` or wrapped in slashes (`/pay.*rent/`).
  The dialect is what Python's `re` and PostgreSQL's engine share: no backreferences, no
  lookarounds. The DB runs it (`__regex`: `~` on PostgreSQL, a Python `REGEXP` function on SQLite),
  never `__iregex`: the text is already lower-case. The pattern is always a query parameter.

On PostgreSQL the query runs under a 1 second `statement_timeout` (`search_guard`), the guard
against a pattern that is too expensive (nested quantifiers are not detected statically).

Every refusal is a `django.core.exceptions.ValidationError` with a message for the user.
"""

import re
from collections.abc import Callable, Generator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Protocol

from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.db import connection, transaction
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, QuerySet, Value, When
from django.db.utils import DataError, OperationalError
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

from apps.accounts.models import User
from apps.core.markdown import normalize_for_search

from ..models import Comment, Task
from .dates import SORT_DEFAULT, order_tasks

MAX_QUERY_CHARS = 200
PAGE_SIZE = 50
MODES = ('glob', 'regex')
#: Milliseconds a PostgreSQL search may run.
STATEMENT_TIMEOUT_MS = 1000

#: Characters of context shown either side of a match, and how much of a long match is highlighted.
SNIPPET_CONTEXT = 60
SNIPPET_MARK_MAX = 100
#: The most normalised text the snippet's regex reads from one field.
SNIPPET_SCAN_MAX = 20_000

EMPTY_MESSAGE = 'Type something to search for.'
TOO_LONG_MESSAGE = 'The pattern is too long.'
INVALID_MESSAGE = 'Invalid regular expression.'
TIMEOUT_MESSAGE = 'That search took too long or is not a valid pattern. Simplify it and try again.'


class Narrowing(Protocol):
    """What `search_tasks` needs of the structured filters (`filters.FilterParams`)."""

    @property
    def sort(self) -> str: ...

    def narrow(self, queryset: QuerySet, user: User) -> QuerySet: ...


@dataclass(frozen=True, slots=True)
class PreparedQuery:
    """A validated query: the `mode` it ended up in, the regular expression (`pattern`, in
    Python's dialect, also what the snippets use) and the compiled form."""

    mode: str
    pattern: str
    regex: re.Pattern[str]

    @property
    def db_pattern(self) -> str:
        """The pattern for the database: PostgreSQL reads `\\b` as a backspace and spells the word
        boundary `\\y`."""
        return to_postgres(self.pattern) if connection.vendor == 'postgresql' else self.pattern


def glob_to_regex(q: str) -> str:
    """The regular expression of the glob `q`: everything escaped with `re.escape`, then `*`
    becomes `.*` and `?` becomes `.`. Unanchored: it matches anywhere in the text."""
    return re.escape(q).replace(r'\*', '.*').replace(r'\?', '.')


def normalize_glob(q: str) -> str:
    """`q` normalised like the stored text (`normalize_for_search`) except that `*` and `?` stay
    wildcards. A piece between wildcards keeps one space where it began or ended with a
    separator (`pay *` still means "pay", a space, anything), and a run of `*` is one."""
    parts: list[str] = []
    for piece in re.split(r'([*?])', q):
        if not piece:
            continue
        if piece in ('*', '?'):
            parts.append(piece)
            continue
        text = normalize_for_search(piece)
        if not text:
            parts.append(' ')
            continue
        lead = ' ' if not normalize_for_search(piece[0]) else ''
        trail = ' ' if not normalize_for_search(piece[-1]) else ''
        parts.append(f'{lead}{text}{trail}')
    joined = re.sub(r' {2,}', ' ', ''.join(parts)).strip()
    return re.sub(r'\*{2,}', '*', joined)


def _has_unsupported_syntax(pattern: str) -> bool:
    """Whether `pattern` has a backreference (`\\1` to `\\9`, `(?P=name)`, `(?(1)...)`) or a
    lookaround: constructs PostgreSQL's engine does not share with Python's."""
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == '\\':
            following = pattern[index + 1 : index + 2]
            if following and following in '123456789':
                return True
            index += 2
            continue
        if char == '(' and pattern.startswith(('(?=', '(?!', '(?<=', '(?<!', '(?P=', '(?('), index):
            return True
        index += 1
    return False


def to_postgres(pattern: str) -> str:
    """`pattern` for PostgreSQL: Python's `\\b` and `\\B` become `\\y` and `\\Y` outside a
    bracket expression (inside one they mean a backspace in both)."""
    out: list[str] = []
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == '\\' and index + 1 < len(pattern):
            following = pattern[index + 1]
            if not in_class and following in 'bB':
                out.append('\\y' if following == 'b' else '\\Y')
            else:
                out.append(char + following)
            index += 2
            continue
        if char == '[' and not in_class:
            in_class = True
        elif char == ']' and in_class:
            in_class = False
        out.append(char)
        index += 1
    return ''.join(out)


def prepare_query(q: str, mode: str = 'glob') -> PreparedQuery:
    """Validate and compile `q`. A query wrapped in slashes (`/.../`, at least one character
    between) is a regular expression whatever `mode` says. Raises `ValidationError`."""
    text = str(q or '').strip()
    if len(text) >= 3 and text.startswith('/') and text.endswith('/'):
        mode, text = 'regex', text[1:-1]
    if mode != 'regex':
        mode = 'glob'
    if len(text) > MAX_QUERY_CHARS:
        raise ValidationError(TOO_LONG_MESSAGE)
    if mode == 'regex':
        if not text:
            raise ValidationError(EMPTY_MESSAGE)
        if _has_unsupported_syntax(text):
            raise ValidationError(INVALID_MESSAGE)
        pattern = text
    else:
        normalised = normalize_glob(text)
        if not normalised:
            raise ValidationError(EMPTY_MESSAGE)
        pattern = glob_to_regex(normalised)
    try:
        regex = re.compile(pattern)
    except re.error, OverflowError, RecursionError:
        raise ValidationError(INVALID_MESSAGE) from None
    return PreparedQuery(mode=mode, pattern=pattern, regex=regex)


@contextmanager
def search_guard() -> Generator[None]:
    """Wrap everything that evaluates a search query. On PostgreSQL it opens a transaction with
    `SET LOCAL statement_timeout` (1 s), and turns a timeout or a pattern the server rejects into
    a `ValidationError`; elsewhere (SQLite, whose `REGEXP` is Python's engine) it does nothing."""
    if connection.vendor != 'postgresql':
        yield
        return
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET LOCAL statement_timeout = %s', [str(STATEMENT_TIMEOUT_MS)])
            yield
    except OperationalError as exc:
        if 'statement timeout' in str(exc):
            raise ValidationError(TIMEOUT_MESSAGE) from exc
        raise
    except DataError as exc:
        raise ValidationError(TIMEOUT_MESSAGE) from exc


def search_queryset(
    user: User, *, q: str, mode: str = 'glob', include_completed: bool = False
) -> QuerySet:
    """The user's non-trashed tasks (open ones only unless `include_completed`) whose title,
    description or a non-deleted comment matches `q`; unordered. Validates `q` now; the query
    itself runs when evaluated -- inside `search_guard()`."""
    query = prepare_query(q, mode)
    pattern = query.db_pattern
    queryset = Task.objects.filter(user=user, deleted_at__isnull=True)
    if not include_completed:
        queryset = queryset.filter(completed_at__isnull=True)
    # An existence test, not a join: a join would repeat a task once per matching comment and
    # multiply the subtask counts a list annotates.
    comment_match = Exists(
        Comment.objects.filter(
            task=OuterRef('pk'), deleted_at__isnull=True, body_text__regex=pattern
        )
    )
    return queryset.filter(
        Q(title_text__regex=pattern) | Q(description_text__regex=pattern) | comment_match
    )


def order_results(queryset: QuerySet, sort: str = SORT_DEFAULT) -> QuerySet:
    """`queryset` in the foundation's `sort` order, completed tasks after open ones."""
    ordered = order_tasks(queryset, sort)
    open_first = Case(
        When(completed_at__isnull=False, then=Value(1)),
        default=Value(0),
        output_field=IntegerField(),
    )
    return ordered.order_by(open_first, *ordered.query.order_by)


# -- Snippets ------------------------------------------------------------------------------------


def make_snippet(text: str, match: re.Match[str]) -> SafeString:
    """`SNIPPET_CONTEXT` characters of `text` either side of `match`, the match in `<mark>`
    (HTML-escaped; ellipses where the text goes on). A long match highlights its first
    `SNIPPET_MARK_MAX` characters."""
    start, end = match.span()
    end = min(end, start + SNIPPET_MARK_MAX)
    left = max(0, start - SNIPPET_CONTEXT)
    right = min(len(text), end + SNIPPET_CONTEXT)
    hit = f'<mark>{escape(text[start:end])}</mark>' if end > start else ''
    html = (
        ('…' if left > 0 else '')
        + escape(text[left:start])
        + hit
        + escape(text[end:right])
        + ('…' if right < len(text) else '')
    )
    return mark_safe(html)  # every piece of the text is escaped above


def attach_matches(tasks: list[Task], query: PreparedQuery | None) -> None:
    """Set `search_field` and `search_snippet` on each task: where `query` matches (title, then
    description, then the first non-deleted comment that does), computed in Python on the
    normalised text with the very regex the database ran. Both are empty without a query or when
    Python finds nothing (the engines differ at the edges)."""
    for task in tasks:
        task.search_field = ''
        task.search_snippet = SafeString('')
    if query is None or not tasks:
        return
    comments: dict[object, list[str]] = {}
    for task_id, body_text in Comment.objects.filter(
        task__in=[task.pk for task in tasks], deleted_at__isnull=True
    ).values_list('task_id', 'body_text'):
        comments.setdefault(task_id, []).append(body_text)
    for task in tasks:
        candidates = [('title', task.title_text), ('description', task.description_text)]
        candidates += [('comment', text) for text in comments.get(task.pk, [])]
        for field, text in candidates:
            match = query.regex.search(text[:SNIPPET_SCAN_MAX]) if text else None
            if match is not None:
                task.search_field = field
                task.search_snippet = make_snippet(text, match)
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
