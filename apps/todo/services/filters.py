"""The structured filters of a search and of a saved filter (issue #181).

`FilterParams` is one value for everything the search page and a `SavedFilter` can say: the search
text (`q`, `mode`, `include_completed`), priorities, labels, a date range and a sort. It is read
*leniently* from a query string or a stored dict (a bad value is dropped, like Receipts'
`parse_filters`), written back in a canonical form (`to_query`, `to_dict`: defaults left out, lists
sorted), and applied by `filter_tasks`. The canonical URL is `/todo/search/?<to_query()>`.
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
from apps.accounts.services.timezones import user_today

from ..models import Label, Task
from .dates import SORT_DEFAULT, SORTS
from .search import (
    MODES,
    PreparedQuery,
    order_results,
    paginate,
    prepare_query,
    search_condition,
    search_queryset,
)

PRIORITIES = (0, 1, 2, 3)
#: A task without a label, in `labels`.
NO_LABEL = 'none'
MAX_LABELS = 50

DATE_ANY = 'any'
DATE_CUSTOM = 'custom'
#: The date ranges, with their names for the filter bar. Presets are resolved against the user's
#: "today" on every run, so a saved "Next 7 days" stays relative.
DATE_RANGES = {
    DATE_ANY: 'Any date',
    'overdue': 'Overdue',
    'today': 'Today',
    'next_7_days': 'Next 7 days',
    'next_30_days': 'Next 30 days',
    'no_date': 'No date',
    DATE_CUSTOM: 'Custom',
}
_NEXT_DAYS = {'next_7_days': 7, 'next_30_days': 30}
_TRUE = frozenset({'1', 'true', 'on', 'yes'})
#: The query-string keys of a `FilterParams`: a request that has none of them asks for nothing.
QUERY_KEYS = (
    'q',
    'mode',
    'include_completed',
    'priorities',
    'labels',
    'date_range',
    'date_from',
    'date_to',
    'sort',
)


def _tokens(values: Iterable[Any]) -> list[str]:
    """Every value split on commas and stripped (`['1,2', '3']` -> `['1', '2', '3']`)."""
    tokens: list[str] = []
    for value in values:
        tokens += [part.strip() for part in str(value).split(',')]
    return [token for token in tokens if token]


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _label_id(token: str) -> str | None:
    if token.lower() == NO_LABEL:
        return NO_LABEL
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
    include_completed: bool = False
    #: A subset of 0..3 (0 is "no priority"), sorted; any of them matches. Empty: any.
    priorities: tuple[int, ...] = ()
    #: Label ids of the user, sorted, and `none` for unlabelled; a task with any of them matches.
    labels: tuple[str, ...] = ()
    date_range: str = DATE_ANY
    #: Only with `date_range == 'custom'`; either may be missing (open-ended).
    date_from: date | None = None
    date_to: date | None = None
    sort: str = SORT_DEFAULT

    # -- Reading ----------------------------------------------------------------------------

    @classmethod
    def from_raw(
        cls, raw: Mapping[str, list[Any]], *, valid_label_ids: set[str] | None = None
    ) -> 'FilterParams':
        """Build from `{key: [values]}` leniently: anything that doesn't validate is dropped.
        `valid_label_ids` (the user's label ids as strings), when given, drops label ids that
        aren't among them (a deleted label, someone else's)."""

        def first(key: str) -> str:
            values = raw.get(key) or []
            return str(values[0]) if values else ''

        q = first('q').strip()
        mode = first('mode').strip().lower()
        include = first('include_completed').strip().lower() in _TRUE

        priorities: set[int] = set()
        for token in _tokens(raw.get('priorities') or []):
            if token.isdigit() and int(token) in PRIORITIES:
                priorities.add(int(token))

        labels: list[str] = []
        for token in _tokens(raw.get('labels') or []):
            label_id = _label_id(token)
            if label_id is None or label_id in labels:
                continue
            if valid_label_ids is not None and label_id != NO_LABEL:
                if label_id not in valid_label_ids:
                    continue
            labels.append(label_id)

        date_range = first('date_range').strip().lower()
        date_from = _date(first('date_from'))
        date_to = _date(first('date_to'))
        if date_range not in DATE_RANGES:
            # Dates without a range: a hand-made link, read as a custom range.
            date_range = DATE_CUSTOM if date_from or date_to else DATE_ANY
        if date_range != DATE_CUSTOM:
            date_from = date_to = None
        elif date_from is None and date_to is None:
            date_range = DATE_ANY

        sort = first('sort').strip()
        return cls(
            q=q,
            mode='regex' if mode == 'regex' else 'glob',
            include_completed=include,
            priorities=tuple(sorted(priorities)),
            labels=tuple(sorted(labels)),
            date_range=date_range,
            date_from=date_from,
            date_to=date_to,
            sort=sort if sort in SORTS else SORT_DEFAULT,
        )

    @classmethod
    def from_query(cls, query: Any, *, valid_label_ids: set[str] | None = None) -> 'FilterParams':
        """From a `QueryDict` (a GET or a POST)."""
        raw = {key: query.getlist(key) for key in QUERY_KEYS}
        return cls.from_raw(raw, valid_label_ids=valid_label_ids)

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any] | None, *, valid_label_ids: set[str] | None = None
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
        return cls.from_raw(raw, valid_label_ids=valid_label_ids)

    # -- Writing ----------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The JSON-able dict a `SavedFilter` stores. Complete (defaults included), so what is
        stored doesn't depend on what the defaults are today."""
        return {
            'q': self.q,
            'mode': self.mode,
            'include_completed': self.include_completed,
            'priorities': list(self.priorities),
            'labels': list(self.labels),
            'date_range': self.date_range,
            'date_from': self.date_from.isoformat() if self.date_from else None,
            'date_to': self.date_to.isoformat() if self.date_to else None,
            'sort': self.sort,
        }

    def to_pairs(self, *, keep_sort: bool = False) -> list[tuple[str, str]]:
        """The query-string pairs, defaults left out (`keep_sort` writes the sort anyway)."""
        pairs: list[tuple[str, str]] = []
        if self.q:
            pairs.append(('q', self.q))
        if self.mode != 'glob':
            pairs.append(('mode', self.mode))
        if self.include_completed:
            pairs.append(('include_completed', '1'))
        pairs += [('priorities', str(priority)) for priority in self.priorities]
        pairs += [('labels', label) for label in self.labels]
        if self.date_range != DATE_ANY:
            pairs.append(('date_range', self.date_range))
        if self.date_from:
            pairs.append(('date_from', self.date_from.isoformat()))
        if self.date_to:
            pairs.append(('date_to', self.date_to.isoformat()))
        if self.sort != SORT_DEFAULT or keep_sort:
            pairs.append(('sort', self.sort))
        return pairs

    def to_query(self, *, keep_sort: bool = False, **extra: str | int | None) -> str:
        """The canonical query string. `extra` adds keys after the filters (`page=2`; `None` or
        an empty value is left out)."""
        pairs = self.to_pairs(keep_sort=keep_sort)
        pairs += [(key, str(value)) for key, value in extra.items() if value not in (None, '')]
        return urlencode(pairs)

    @property
    def has_criteria(self) -> bool:
        """Whether anything selects tasks (text, priorities, labels or a date range); the sort
        and the completed switch alone select nothing."""
        return bool(self.q.strip() or self.priorities or self.labels or self.date_range != DATE_ANY)

    @property
    def is_regex(self) -> bool:
        """Whether `q` is read as a regular expression (the mode, or wrapped in slashes)."""
        text = self.q.strip()
        return self.mode == 'regex' or (len(text) >= 3 and text[0] == '/' and text[-1] == '/')

    # -- Applying ---------------------------------------------------------------------------

    def date_bounds(self, today: date) -> tuple[date | None, date | None]:
        """The inclusive `(first, last)` due dates of the range, against `today`; `None` is
        open-ended. `overdue` and `no_date` are not bounds (see `narrow`)."""
        if self.date_range == 'today':
            return today, today
        if self.date_range in _NEXT_DAYS:
            return today, today + timedelta(days=_NEXT_DAYS[self.date_range])
        if self.date_range == DATE_CUSTOM:
            return self.date_from, self.date_to
        return None, None

    def structured_condition(self, user: User) -> Q:
        """The priorities, labels and date range as one condition on a `Task` (empty when they
        select nothing). The labels are an existence test on the through table, not a join, so a
        list's counts aren't multiplied."""
        condition = Q()
        if self.priorities:
            condition &= Q(priority__in=self.priorities)
        if self.labels:
            through = Task.labels.through.objects.filter(task_id=OuterRef('pk'))
            any_label = Q()
            ids = [uuid.UUID(label) for label in self.labels if label != NO_LABEL]
            if ids:
                any_label |= Q(Exists(through.filter(label_id__in=ids)))
            if NO_LABEL in self.labels:
                any_label |= ~Q(Exists(through))
            condition &= any_label
        today = user_today(user)
        if self.date_range == 'overdue':
            condition &= Q(due_date__lt=today, completed_at__isnull=True)
        elif self.date_range == 'no_date':
            condition &= Q(due_date__isnull=True)
        else:
            first, last = self.date_bounds(today)
            if first is not None:
                condition &= Q(due_date__gte=first)
            if last is not None:
                condition &= Q(due_date__lte=last)
        return condition

    def condition(self, user: User) -> Q:
        """Everything `params` selects, text included, as one condition on the user's tasks
        (the search's own check on `q`: raises `ValidationError` for one that can't run). What
        `saved_filters` counts with: several of them in one aggregate."""
        condition = self.structured_condition(user)
        if self.q.strip():
            condition &= search_condition(prepare_query(self.q, self.mode).db_pattern)
        return condition

    def narrow(self, queryset: QuerySet, user: User) -> QuerySet:
        """`queryset` (of the user's tasks) restricted by the priorities, labels and date range."""
        return queryset.filter(self.structured_condition(user))

    def with_changes(self, **changes: Any) -> 'FilterParams':
        return replace(self, **changes)


def parse_filter_params(query: Any, user: User | None = None) -> FilterParams:
    """`FilterParams` from a `QueryDict`; with `user`, label ids that aren't theirs are dropped."""
    return FilterParams.from_query(query, valid_label_ids=label_ids(user) if user else None)


def label_ids(user: User) -> set[str]:
    """The user's label ids, as strings."""
    return {str(pk) for pk in Label.objects.filter(user=user).values_list('pk', flat=True)}


def label_names(user: User) -> dict[str, str]:
    """`{label id: name}` of the user's labels."""
    return {str(pk): name for pk, name in Label.objects.filter(user=user).values_list('pk', 'name')}


def filter_tasks(user: User, params: FilterParams, *, ordered: bool = True) -> QuerySet:
    """The user's tasks that `params` selects, in its sort order (completed ones last) unless
    `ordered` is false. With `q` it is the search (`search.search_queryset`, validated now, run
    when the queryset is evaluated -- inside `search.search_guard()`); without it, open tasks
    unless `include_completed`. Raises `ValidationError` for a search that can't run."""
    if params.q.strip():
        queryset = search_queryset(
            user, q=params.q, mode=params.mode, include_completed=params.include_completed
        )
    else:
        queryset = Task.objects.filter(user=user, deleted_at__isnull=True)
        if not params.include_completed:
            queryset = queryset.filter(completed_at__isnull=True)
    queryset = params.narrow(queryset, user)
    return order_results(queryset, params.sort) if ordered else queryset


def filter_page(
    user: User,
    params: FilterParams,
    *,
    page: int | str | None = 1,
    decorate: Callable[[QuerySet], QuerySet] | None = None,
) -> Page:
    """A page of `filter_tasks`, with the matches of `q` attached to each task."""
    query: PreparedQuery | None = None
    if params.q.strip():
        query = prepare_query(params.q, params.mode)
    return paginate(filter_tasks(user, params), query=query, page=page, decorate=decorate)


# -- Describing ----------------------------------------------------------------------------------


def _day(value: date) -> str:
    return f'{value.day} {value:%b} {value.year}'


def describe_params(params: FilterParams, names: Mapping[str, str] | None = None) -> str:
    """A short summary: `priority 1 · @home · next 7 days · "rent*"`. `names` maps label ids
    to names (`label_names(user)`); a label it doesn't know is left out."""
    parts: list[str] = []
    if params.priorities:
        levels = ['none' if priority == 0 else str(priority) for priority in params.priorities]
        parts.append(f'priority {", ".join(levels)}')
    names_by_id = names or {}
    for label in params.labels:
        if label == NO_LABEL:
            parts.append('no label')
        elif label in names_by_id:
            parts.append(f'@{names_by_id[label]}')
    if params.date_range == DATE_CUSTOM:
        if params.date_from and params.date_to:
            parts.append(f'{_day(params.date_from)} to {_day(params.date_to)}')
        elif params.date_from:
            parts.append(f'from {_day(params.date_from)}')
        elif params.date_to:
            parts.append(f'until {_day(params.date_to)}')
    elif params.date_range != DATE_ANY:
        parts.append(DATE_RANGES[params.date_range].lower())
    text = params.q.strip()
    if text:
        if params.is_regex:
            pattern = text[1:-1] if len(text) >= 3 and text[0] == text[-1] == '/' else text
            parts.append(f'/{pattern}/')
        else:
            parts.append(f'"{text}"')
    if params.include_completed:
        parts.append('including completed')
    if params.sort != SORT_DEFAULT:
        parts.append(f'by {SORTS[params.sort].lower()}')
    return ' · '.join(parts) or 'all open tasks'


__all__ = [
    'DATE_ANY',
    'DATE_CUSTOM',
    'DATE_RANGES',
    'MODES',
    'NO_LABEL',
    'PRIORITIES',
    'QUERY_KEYS',
    'FilterParams',
    'describe_params',
    'filter_page',
    'filter_tasks',
    'label_ids',
    'label_names',
    'parse_filter_params',
]
