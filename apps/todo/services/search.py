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

The snippets are the other place a user's pattern is run, and PostgreSQL's engine does not protect
Python's: its DFA answers `a.*a.*a.*a.*c` or `(a|aa)+$` at once, while a backtracking engine takes
minutes on the same text. So they are computed with the third-party `regex` package, whose
`search(timeout=...)` is bounded (`attach_matches`): a time budget for the whole page
(`SNIPPET_TIME_BUDGET`), only the field the database said matched, and at most `SNIPPET_SCAN_MAX`
characters of it. A row past the budget is still listed, without a snippet.

Every refusal is a `django.core.exceptions.ValidationError` with a message for the user.
"""

import re
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Protocol

import regex
from django.core.exceptions import ValidationError
from django.core.paginator import Page, Paginator
from django.db import connection, transaction
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
SNIPPET_SCAN_MAX = 5_000
#: Seconds all the snippets of one page may take together; each search gets what is left, and a
#: row that finds the budget spent (or times out) is listed without a snippet.
SNIPPET_TIME_BUDGET = 0.2

EMPTY_MESSAGE = 'Type something to search for.'
TOO_LONG_MESSAGE = 'The pattern is too long.'
INVALID_MESSAGE = 'Invalid regular expression.'
TIMEOUT_MESSAGE = 'That search took too long or is not a valid pattern. Simplify it and try again.'


class Narrowing(Protocol):
    """What `search_tasks` needs of the structured filters (`filters.FilterParams`)."""

    @property
    def sort(self) -> str: ...

    def narrow(self, queryset: QuerySet, user: User) -> QuerySet: ...


class SpanMatch(Protocol):
    """What a snippet needs of a match (`re.Match` and `regex.Match` both have it)."""

    def span(self) -> tuple[int, int]: ...


class BoundedPattern(Protocol):
    """A compiled `regex` pattern: `search` raises `TimeoutError` after `timeout` seconds."""

    def search(self, text: str, *, timeout: float) -> SpanMatch | None: ...


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


#: Group openers neither engine pair shares: lookarounds, backreferences, conditionals, and
#: Python-only syntax PostgreSQL's engine rejects (atomic groups, named groups).
_UNSUPPORTED_GROUPS = ('(?=', '(?!', '(?<=', '(?<!', '(?P=', '(?(', '(?>', '(?P<')
#: A counted repetition in Python's dialect: `{3}`, `{3,}`, `{3,5}`, `{,5}`.
_COUNTED = re.compile(r'\{(?:\d+(?:,\d*)?|,\d+)\}')


def _class_body_start(pattern: str, opener: int) -> int:
    """Index of the first character after the `[` (and `^`) at `opener`: a `]` there is a
    literal, not the end of the bracket expression."""
    return opener + (2 if pattern.startswith('^', opener + 1) else 1)


def _has_unsupported_syntax(pattern: str) -> bool:
    """Whether `pattern` uses something PostgreSQL's engine does not share with Python's: a
    backreference (`\\1` to `\\9`), a lookaround, a conditional, or Python-only syntax (atomic
    groups `(?>...)`, possessive quantifiers `a*+`, named groups `(?P<n>...)`, `\\N{...}`).
    Escapes are skipped and bracket expressions are read as such, so `\\\\1` and `[(?=]` are fine."""
    index = 0
    in_class = False
    class_body = 0
    while index < len(pattern):
        char = pattern[index]
        if char == '\\':
            following = pattern[index + 1 : index + 2]
            if following and following in '123456789':
                return True
            if following == 'N' and pattern.startswith('{', index + 2):
                return True
            index += 2
            continue
        if in_class:
            if char == ']' and index > class_body:
                in_class = False
            index += 1
            continue
        if char == '[':
            in_class = True
            class_body = _class_body_start(pattern, index)
        elif char == '(' and pattern.startswith(_UNSUPPORTED_GROUPS, index):
            return True
        elif char in '*+?' and pattern.startswith('+', index + 1):
            return True  # possessive: `a*+`, `a++`, `a?+`
        elif char == '{':
            counted = _COUNTED.match(pattern, index)
            if counted is not None:
                if pattern.startswith('+', counted.end()):
                    return True  # possessive: `a{2}+`
                index = counted.end()
                continue
        index += 1
    return False


def lower_literals(pattern: str) -> str:
    """`pattern` with its literal characters lower-cased, to match the lower-case stored text
    (`/Rent/` finds "rent"). Safe because only an escape's letter is case-sensitive
    (`\\D` against `\\d`, `\\B`, `\\Z`), and escapes are copied untouched; hex digits after
    `\\x`/`\\u` mean the same either case. A character whose lower case is not one character
    (`İ`) is kept."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == '\\' and index + 1 < len(pattern):
            out.append(pattern[index : index + 2])
            index += 2
            continue
        lowered = char.lower()
        out.append(lowered if len(lowered) == 1 else char)
        index += 1
    return ''.join(out)


def to_postgres(pattern: str) -> str:
    """`pattern` for PostgreSQL: Python's `\\b` and `\\B` become `\\y` and `\\Y` outside a
    bracket expression (inside one they mean a backspace in both)."""
    out: list[str] = []
    in_class = False
    class_body = 0
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
            class_body = _class_body_start(pattern, index)
        elif char == ']' and in_class and index > class_body:
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
        pattern = lower_literals(text)
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
    """Wrap everything that evaluates a search query. On PostgreSQL it opens a transaction (a
    savepoint when one is already open) with `SET LOCAL statement_timeout` (1 s), and turns a
    timeout or a pattern the server rejects into a `ValidationError`; elsewhere (SQLite, whose
    `REGEXP` is Python's engine) it does nothing.

    `SET LOCAL` lasts until the *outermost* transaction ends, and releasing a savepoint keeps it,
    so inside an outer `atomic()` it would put a 1 s limit on everything that follows. The guard
    therefore reads the value it replaces and puts it back, with `set_config(..., true)` (also
    local), before it leaves normally; when it leaves with an error the savepoint (or the
    transaction) is rolled back, which undoes the setting by itself. It never needs to know
    whether it is nested, and nothing outside it sees the timeout."""
    if connection.vendor != 'postgresql':
        yield
        return
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SELECT current_setting(%s)', ['statement_timeout'])
                previous = cursor.fetchone()[0]
                cursor.execute('SET LOCAL statement_timeout = %s', [str(STATEMENT_TIMEOUT_MS)])
            yield
            with connection.cursor() as cursor:
                cursor.execute('SELECT set_config(%s, %s, true)', ['statement_timeout', previous])
    except OperationalError as exc:
        if 'statement timeout' in str(exc):
            raise ValidationError(TIMEOUT_MESSAGE) from exc
        raise
    except DataError as exc:
        raise ValidationError(TIMEOUT_MESSAGE) from exc


def search_condition(pattern: str) -> Q:
    """The condition "title, description or a non-deleted comment matches `pattern`" (the
    database's dialect: `PreparedQuery.db_pattern`) on a `Task`. A condition, not a join: a join
    would repeat a task once per matching comment and multiply the subtask counts a list
    annotates."""
    comment_match = Exists(
        Comment.objects.filter(
            task=OuterRef('pk'), deleted_at__isnull=True, body_text__regex=pattern
        )
    )
    return Q(title_text__regex=pattern) | Q(description_text__regex=pattern) | Q(comment_match)


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
    return queryset.filter(search_condition(query.db_pattern))


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


def make_snippet(text: str, match: SpanMatch) -> SafeString:
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


def snippet_regex(pattern: str) -> BoundedPattern | None:
    """`pattern` (Python's dialect) compiled with the `regex` package, whose searches can be given
    a timeout, or `None` when it can't be compiled (the page then has no snippets). Compiling is
    cheap whatever the pattern; only matching is bounded."""
    try:
        return regex.compile(pattern)
    except regex.error, OverflowError, RecursionError, ValueError, MemoryError:
        return None


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
    """Set `search_field` and `search_snippet` on each task: where `query` matches (title, then
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
