"""The structured filters of a search and of a saved filter (issue #260, PR 5; To-do's #181).

`FilterParams` is one value for everything the search page and a `SavedFilter` can say: the search
text (`q`, `mode`), the scope (`active`, `archived`, `all`: never the Trash), tags, whether the
note is shared, an `updated` range and a sort. It is read *leniently* from a query string or a
stored dict (a bad value is dropped), written back in a canonical form (`to_query`, `to_dict`:
defaults left out, lists sorted) and applied by `filter_notes`. The canonical URL is
`/notes/search/?<to_query()>`.

`updated` ranges are days in the **user's zone** (`user_today`, `local_datetime`) resolved on every
run, so a saved "Last 7 days" stays relative: `today`, `last_7_days` (today and the six days
before, like Receipts' presets), `last_30_days`, and `custom` with `updated_from` / `updated_to`
(inclusive days, either may be missing; a start after the end is read swapped).

The sort has a direction, `dir` (`asc` or `desc`), with each sort's own default
(`lists.DEFAULT_DIRECTIONS`: `updated` and `created` newest first, `title` and `manual` ascending);
`lists.sort_direction` is the one reader both the lists and the search use. It is written (`to_query`,
`to_dict`) only when it isn't the sort's default, so a filter saved before it reads back the same.
"""

import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from django.core.paginator import Page
from django.db.models import Exists, OuterRef, Q, QuerySet

from apps.accounts.models import User
from apps.accounts.services.timezones import local_datetime, user_today
from apps.core.datetime_format import DisplayFormats, format_date

from ..models import Note, Tag
from . import links
from .lists import DEFAULT_DIRECTIONS, DIRECTIONS, SORTS, sort_direction
from .search import (
    MODES,
    SCOPE_DEFAULT,
    SCOPES,
    PreparedQuery,
    order_results,
    paginate,
    prepare_query,
    scope_condition,
    scoped_notes,
    search_condition,
    search_queryset,
)

SORT_DEFAULT = 'updated'
#: A note without a tag, in `tags`.
NO_TAG = 'none'
SHARED_VALUES = ('', 'yes', 'no')

UPDATED_ANY = 'any'
UPDATED_CUSTOM = 'custom'
#: The updated ranges, with their names for the filter bar. Presets are resolved against the user's
#: "today" on every run, so a saved "Last 7 days" stays relative.
UPDATED_RANGES = {
    UPDATED_ANY: 'Any time',
    'today': 'Today',
    'last_7_days': 'Last 7 days',
    'last_30_days': 'Last 30 days',
    UPDATED_CUSTOM: 'Custom',
}
_LAST_DAYS = {'last_7_days': 7, 'last_30_days': 30}
_TRUE = frozenset({'1', 'true', 'on', 'yes'})
#: The query-string keys of a `FilterParams`: a request that has none of them asks for nothing.
QUERY_KEYS = (
    'q',
    'mode',
    'scope',
    'tags',
    'shared',
    'updated',
    'updated_from',
    'updated_to',
    'sort',
    'dir',
)


def _tokens(values: Iterable[Any]) -> list[str]:
    """Every value split on commas and stripped (`['a,b', 'c']` -> `['a', 'b', 'c']`)."""
    tokens: list[str] = []
    for value in values:
        tokens += [part.strip() for part in str(value).split(',')]
    return [token for token in tokens if token]


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _tag_id(token: str) -> str | None:
    if token.lower() == NO_TAG:
        return NO_TAG
    try:
        return str(uuid.UUID(token))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class FilterParams:
    """What a search and a saved filter select. Immutable; build one with `from_query` /
    `from_dict` (lenient) or by hand."""

    q: str = ''
    mode: str = 'glob'
    #: `active` (not archived), `archived` or `all` (both): never the Trash.
    scope: str = SCOPE_DEFAULT
    #: Tag ids of the user, sorted, and `none` for untagged; a note with any of them matches.
    tags: tuple[str, ...] = ()
    #: `yes` (the note has an active link that works), `no` (it doesn't), or empty (either).
    shared: str = ''
    updated: str = UPDATED_ANY
    #: Only with `updated == 'custom'`; either may be missing (open-ended).
    updated_from: date | None = None
    updated_to: date | None = None
    sort: str = SORT_DEFAULT
    #: `asc` or `desc`, or empty for the sort's own default (`direction` is the effective one). Kept
    #: empty whenever it equals the default, so two params that mean the same compare equal.
    dir: str = ''

    def __post_init__(self) -> None:
        wanted = self.dir if self.dir in DIRECTIONS else ''
        if wanted == DEFAULT_DIRECTIONS.get(self.sort):
            wanted = ''
        if wanted != self.dir:
            object.__setattr__(self, 'dir', wanted)

    @property
    def direction(self) -> str:
        """The direction the sort runs in: `dir`, or the sort's default."""
        return sort_direction(self.sort, self.dir or None)

    # -- Reading ----------------------------------------------------------------------------

    @classmethod
    def from_raw(
        cls, raw: Mapping[str, list[Any]], *, valid_tag_ids: set[str] | None = None
    ) -> 'FilterParams':
        """Build from `{key: [values]}` leniently: anything that doesn't validate is dropped.
        `valid_tag_ids` (the user's tag ids as strings), when given, drops tag ids that aren't
        among them (a deleted tag, someone else's)."""

        def first(key: str) -> str:
            values = raw.get(key) or []
            return str(values[0]) if values else ''

        q = first('q').strip()
        mode = first('mode').strip().lower()

        scope = first('scope').strip().lower()
        if scope not in SCOPES:
            scope = SCOPE_DEFAULT

        tags: list[str] = []
        for token in _tokens(raw.get('tags') or []):
            tag_id = _tag_id(token)
            if tag_id is None or tag_id in tags:
                continue
            if valid_tag_ids is not None and tag_id != NO_TAG and tag_id not in valid_tag_ids:
                continue
            tags.append(tag_id)

        shared = first('shared').strip().lower()
        if shared in _TRUE:
            shared = 'yes'
        if shared not in SHARED_VALUES:
            shared = ''

        updated = first('updated').strip().lower()
        updated_from = _date(first('updated_from'))
        updated_to = _date(first('updated_to'))
        if updated not in UPDATED_RANGES:
            # Dates without a range: a hand-made link, read as a custom range.
            updated = UPDATED_CUSTOM if updated_from or updated_to else UPDATED_ANY
        if updated != UPDATED_CUSTOM:
            updated_from = updated_to = None
        elif updated_from is None and updated_to is None:
            updated = UPDATED_ANY
        elif updated_from is not None and updated_to is not None and updated_from > updated_to:
            # A range typed backwards means the days between them, not nothing.
            updated_from, updated_to = updated_to, updated_from

        sort = first('sort').strip()
        if sort not in SORTS:
            sort = SORT_DEFAULT
        return cls(
            q=q,
            mode='regex' if mode == 'regex' else 'glob',
            scope=scope,
            tags=tuple(sorted(tags)),
            shared=shared,
            updated=updated,
            updated_from=updated_from,
            updated_to=updated_to,
            sort=sort,
            dir=first('dir').strip().lower(),
        )

    @classmethod
    def from_query(cls, query: Any, *, valid_tag_ids: set[str] | None = None) -> 'FilterParams':
        """From a `QueryDict` (a GET or a POST)."""
        raw = {key: query.getlist(key) for key in QUERY_KEYS}
        return cls.from_raw(raw, valid_tag_ids=valid_tag_ids)

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any] | None, *, valid_tag_ids: set[str] | None = None
    ) -> 'FilterParams':
        """From a stored dict (`to_dict`), leniently."""
        raw: dict[str, list[Any]] = {}
        for key, value in (data or {}).items():
            if isinstance(value, (list, tuple)):
                raw[key] = list(value)
            elif isinstance(value, bool):
                raw[key] = ['1' if value else '']
            elif value is not None:
                raw[key] = [value]
        return cls.from_raw(raw, valid_tag_ids=valid_tag_ids)

    # -- Writing ----------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The JSON-able dict a `SavedFilter` stores. Complete (defaults included), so what is
        stored doesn't depend on what the defaults are today."""
        data: dict[str, Any] = {
            'q': self.q,
            'mode': self.mode,
            'scope': self.scope,
            'tags': list(self.tags),
            'shared': self.shared,
            'updated': self.updated,
            'updated_from': self.updated_from.isoformat() if self.updated_from else None,
            'updated_to': self.updated_to.isoformat() if self.updated_to else None,
            'sort': self.sort,
        }
        if self.dir:  # only a changed direction: a filter saved before it reads back the same
            data['dir'] = self.dir
        return data

    def to_pairs(self, *, keep_sort: bool = False) -> list[tuple[str, str]]:
        """The query-string pairs, defaults left out (`keep_sort` writes the sort anyway)."""
        pairs: list[tuple[str, str]] = []
        if self.q:
            pairs.append(('q', self.q))
        if self.mode != 'glob':
            pairs.append(('mode', self.mode))
        if self.scope != SCOPE_DEFAULT:
            pairs.append(('scope', self.scope))
        pairs += [('tags', tag) for tag in self.tags]
        if self.shared:
            pairs.append(('shared', self.shared))
        if self.updated != UPDATED_ANY:
            pairs.append(('updated', self.updated))
        if self.updated_from:
            pairs.append(('updated_from', self.updated_from.isoformat()))
        if self.updated_to:
            pairs.append(('updated_to', self.updated_to.isoformat()))
        if self.sort != SORT_DEFAULT or keep_sort:
            pairs.append(('sort', self.sort))
        if self.dir:
            pairs.append(('dir', self.dir))
        return pairs

    def to_query(self, *, keep_sort: bool = False, **extra: str | int | None) -> str:
        """The canonical query string. `extra` adds keys after the filters (`page=2`; `None` or
        an empty value is left out)."""
        pairs = self.to_pairs(keep_sort=keep_sort)
        pairs += [(key, str(value)) for key, value in extra.items() if value not in (None, '')]
        return urlencode(pairs)

    @property
    def has_criteria(self) -> bool:
        """Whether anything selects notes (text, tags, the shared switch or an updated range);
        the scope and the sort alone select nothing."""
        return bool(self.q.strip() or self.tags or self.shared or self.updated != UPDATED_ANY)

    @property
    def is_regex(self) -> bool:
        """Whether `q` is read as a regular expression (the mode, or wrapped in slashes)."""
        text = self.q.strip()
        return self.mode == 'regex' or (len(text) >= 3 and text[0] == '/' and text[-1] == '/')

    # -- Applying ---------------------------------------------------------------------------

    def date_bounds(self, today: date) -> tuple[date | None, date | None]:
        """The inclusive `(first, last)` days of the updated range, against `today`; `None` is
        open-ended."""
        if self.updated == 'today':
            return today, today
        if self.updated in _LAST_DAYS:
            return today - timedelta(days=_LAST_DAYS[self.updated] - 1), today
        if self.updated == UPDATED_CUSTOM:
            return self.updated_from, self.updated_to
        return None, None

    def structured_condition(self, user: User) -> Q:
        """The tags, the shared switch and the updated range as one condition on a `Note` (empty
        when they select nothing; the scope is not here: see `scoped_notes`). The tags are an
        existence test on the through table, not a join, so a list's counts aren't multiplied."""
        condition = Q()
        if self.tags:
            through = Note.tags.through.objects.filter(note_id=OuterRef('pk'))
            any_tag = Q()
            ids = [uuid.UUID(tag) for tag in self.tags if tag != NO_TAG]
            if ids:
                any_tag |= Q(Exists(through.filter(tag_id__in=ids)))
            if NO_TAG in self.tags:
                any_tag |= ~Q(Exists(through))
            condition &= any_tag
        if self.shared == 'yes':
            condition &= links.shared_condition()
        elif self.shared == 'no':
            condition &= ~links.shared_condition()
        first, last = self.date_bounds(user_today(user))
        if first is not None:
            condition &= Q(updated_at__gte=local_datetime(user, first, None))
        if last is not None:
            # Up to the start of the next day in the user's zone: the whole of `last`.
            condition &= Q(updated_at__lt=local_datetime(user, last + timedelta(days=1), None))
        return condition

    def condition(self, user: User) -> Q:
        """Everything `params` selects -- scope, text and structured filters -- as one condition on
        the user's notes outside the Trash (the search's own check on `q`: raises
        `ValidationError` for one that can't run). What `saved_filters` counts with: several of
        them in one aggregate."""
        condition = scope_condition(self.scope) & self.structured_condition(user)
        if self.q.strip():
            query = prepare_query(self.q, self.mode)
            condition &= search_condition(query.db_pattern)
        return condition

    def narrow(self, queryset: QuerySet, user: User) -> QuerySet:
        """`queryset` (of the user's notes) restricted by the tags, shared switch and updated
        range."""
        return queryset.filter(self.structured_condition(user))

    def with_changes(self, **changes: Any) -> 'FilterParams':
        return replace(self, **changes)


def parse_filter_params(query: Any, user: User | None = None) -> FilterParams:
    """`FilterParams` from a `QueryDict`; with `user`, tag ids that aren't theirs are dropped."""
    return FilterParams.from_query(query, valid_tag_ids=tag_ids(user) if user else None)


def tag_ids(user: User) -> set[str]:
    """The user's tag ids, as strings."""
    return {str(pk) for pk in Tag.objects.filter(user=user).values_list('pk', flat=True)}


def tag_names(user: User) -> dict[str, str]:
    """`{tag id: name}` of the user's tags."""
    return {str(pk): name for pk, name in Tag.objects.filter(user=user).values_list('pk', 'name')}


def filter_notes(user: User, params: FilterParams, *, ordered: bool = True) -> QuerySet:
    """The user's notes (outside the Trash) that `params` selects, in its sort order unless
    `ordered` is false. With `q` it is the search (`search.search_queryset`, validated now, run
    when the queryset is evaluated -- inside `search.search_guard()`). Raises `ValidationError` for
    a search that can't run."""
    if params.q.strip():
        queryset = search_queryset(user, q=params.q, mode=params.mode, scope=params.scope)
    else:
        queryset = scoped_notes(user, params.scope)
    queryset = params.narrow(queryset, user)
    return order_results(queryset, params.sort, params.dir or None) if ordered else queryset


def filter_page(
    user: User,
    params: FilterParams,
    *,
    page: int | str | None = 1,
    decorate: Callable[[QuerySet], QuerySet] | None = None,
) -> Page:
    """A page of `filter_notes`, with the matches of `q` attached to each note."""
    query: PreparedQuery | None = None
    if params.q.strip():
        query = prepare_query(params.q, params.mode)
    return paginate(filter_notes(user, params), query=query, page=page, decorate=decorate)


# -- Describing ----------------------------------------------------------------------------------


#: How a direction reads in a summary, where it isn't the sort's default.
_DIRECTION_WORDS = {
    ('updated', 'asc'): 'oldest first',
    ('created', 'asc'): 'oldest first',
    ('title', 'desc'): 'Z to A',
    ('manual', 'desc'): 'reversed',
}


def describe_params(
    params: FilterParams,
    names: Mapping[str, str] | None = None,
    formats: DisplayFormats | None = None,
) -> str:
    """A short summary: `@work · shared · last 7 days · "deploy*"`. `names` maps tag ids to names
    (`tag_names(user)`); a tag it doesn't know is left out. Dates are written in `formats` (default:
    the formats active for the request; the API passes `PUBLIC`)."""

    def _day(value: date) -> str:
        return format_date(value, 'full', formats)

    parts: list[str] = []
    names_by_id = names or {}
    for tag in params.tags:
        if tag == NO_TAG:
            parts.append('no tag')
        elif tag in names_by_id:
            parts.append(f'@{names_by_id[tag]}')
    if params.shared == 'yes':
        parts.append('shared')
    elif params.shared == 'no':
        parts.append('not shared')
    if params.updated == UPDATED_CUSTOM:
        if params.updated_from and params.updated_to:
            parts.append(f'{_day(params.updated_from)} to {_day(params.updated_to)}')
        elif params.updated_from:
            parts.append(f'from {_day(params.updated_from)}')
        elif params.updated_to:
            parts.append(f'until {_day(params.updated_to)}')
    elif params.updated != UPDATED_ANY:
        parts.append(UPDATED_RANGES[params.updated].lower())
    text = params.q.strip()
    if text:
        if params.is_regex:
            pattern = text[1:-1] if len(text) >= 3 and text[0] == text[-1] == '/' else text
            parts.append(f'/{pattern}/')
        else:
            parts.append(f'"{text}"')
    if params.scope == 'archived':
        parts.append('archived only')
    elif params.scope == 'all':
        parts.append('including archived')
    if params.sort != SORT_DEFAULT or params.dir:
        word = _DIRECTION_WORDS.get((params.sort, params.direction))
        parts.append(f'by {SORTS[params.sort].lower()}' + (f' ({word})' if word else ''))
    return ' · '.join(parts) or 'all notes'


__all__ = [
    'MODES',
    'NO_TAG',
    'QUERY_KEYS',
    'SCOPES',
    'SHARED_VALUES',
    'SORT_DEFAULT',
    'UPDATED_ANY',
    'UPDATED_CUSTOM',
    'UPDATED_RANGES',
    'FilterParams',
    'describe_params',
    'filter_notes',
    'filter_page',
    'parse_filter_params',
    'tag_ids',
    'tag_names',
]
