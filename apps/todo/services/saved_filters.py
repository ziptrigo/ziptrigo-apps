"""Saved filters (issue #181): a named `FilterParams`, listed in the sidebar.

A refusal is a `django.core.exceptions.ValidationError` keyed by field (`'__all__'` for none), like
the labels'. The sidebar and the filters page show how many open tasks each filter selects. Those
counts are not computed when a page renders: the page asks for them afterwards
(`views.filters.filter_counts_view`), and `filter_counts` answers for all the filters in **one** query
(a conditional `Count` per filter over the user's open tasks) under the search's single
`statement_timeout`; when it times out every count is `None` ("–") rather than one timeout per
filter.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, QuerySet

from apps.accounts.models import User

from ..models import SavedFilter, Task, TodoAccount
from .accounts import get_account
from .filters import FilterParams, describe_params, label_ids, label_names
from .limits import MAX_SAVED_FILTERS, SAVED_FILTER_NAME_MAX
from .search import prepare_query, search_guard

#: Filters the sidebar lists; the rest are "and N more", linking to the filters page.
SIDEBAR_LIMIT = 20
DUPLICATE_MESSAGE = 'You already have a filter with this name.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def clean_name(name: object) -> str:
    """The name with its whitespace collapsed; 1 to `SAVED_FILTER_NAME_MAX` characters."""
    cleaned = ' '.join(str(name or '').split())
    if not cleaned:
        raise _error('name', 'Name is required.')
    if len(cleaned) > SAVED_FILTER_NAME_MAX:
        raise _error('name', f'Name is too long (at most {SAVED_FILTER_NAME_MAX} characters).')
    return cleaned


def _check_unique(user: User, name: str, *, exclude: SavedFilter | None = None) -> None:
    taken = SavedFilter.objects.filter(user=user, name__iexact=name)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise _error('name', DUPLICATE_MESSAGE)


def clean_params(user: User, params: FilterParams | Mapping[str, Any]) -> FilterParams:
    """`params` as the canonical `FilterParams` that would be stored: parsed back leniently (a
    label that isn't the user's is dropped) and, when it has search text, that text validated
    (`search.prepare_query`: raises the search's own `ValidationError`)."""
    data = params.to_dict() if isinstance(params, FilterParams) else params
    cleaned = FilterParams.from_dict(data, valid_label_ids=label_ids(user))
    if cleaned.q.strip():
        prepare_query(cleaned.q, cleaned.mode)
    return cleaned


def stored_params(saved: SavedFilter, user: User | None = None) -> FilterParams:
    """The filter's params, read leniently; with `user`, labels deleted since are dropped."""
    return FilterParams.from_dict(
        saved.params, valid_label_ids=label_ids(user) if user is not None else None
    )


def dropped_label_count(saved: SavedFilter, user: User) -> int:
    """How many labels the filter was saved with that no longer exist (deleted since). They simply
    drop out of the filter when it runs; a filter whose only criterion was one then selects every
    open task, which the pages say instead of leaving it as a surprise."""
    saved_with = set(FilterParams.from_dict(saved.params).labels)
    return len(saved_with - set(stored_params(saved, user).labels))


def list_filters(user: User) -> QuerySet:
    """The user's saved filters in their order."""
    return SavedFilter.objects.filter(user=user).order_by('position', 'created_at')


def save_filter(user: User, name: str, params: FilterParams | Mapping[str, Any]) -> SavedFilter:
    """Save `params` under `name` at the end of the user's list. Names are unique per user,
    ignoring case; at most `MAX_SAVED_FILTERS` filters."""
    cleaned_name = clean_name(name)
    cleaned = clean_params(user, params)
    get_account(user)
    with transaction.atomic():
        TodoAccount.objects.select_for_update().get(pk=user.pk)  # one creation at a time per user
        existing = SavedFilter.objects.filter(user=user)
        if existing.count() >= MAX_SAVED_FILTERS:
            raise _error('__all__', f'You can have up to {MAX_SAVED_FILTERS} saved filters.')
        _check_unique(user, cleaned_name)
        position = (existing.aggregate(top=Max('position'))['top'] or 0) + 1
        try:
            with transaction.atomic():
                return SavedFilter.objects.create(
                    user=user, name=cleaned_name, params=cleaned.to_dict(), position=position
                )
        except IntegrityError:  # a concurrent create of the same name
            raise _error('name', DUPLICATE_MESSAGE) from None


def rename_filter(saved: SavedFilter, name: str) -> SavedFilter:
    cleaned = clean_name(name)
    _check_unique(saved.user, cleaned, exclude=saved)
    saved.name = cleaned
    try:
        with transaction.atomic():
            saved.save(update_fields=['name'])
    except IntegrityError:
        raise _error('name', DUPLICATE_MESSAGE) from None
    return saved


def update_filter_params(
    saved: SavedFilter, params: FilterParams | Mapping[str, Any]
) -> SavedFilter:
    """Replace the filter's params ("Update this filter" from a changed search)."""
    saved.params = clean_params(saved.user, params).to_dict()
    saved.save(update_fields=['params'])
    return saved


def delete_filter(saved: SavedFilter) -> None:
    saved.delete()


def reorder_filters(user: User, ids: Sequence[UUID]) -> int:
    """Write `position = index` for the given ids (the user's own; others are ignored). At most
    `MAX_SAVED_FILTERS` ids: more than a user can have is refused."""
    if len(ids) > MAX_SAVED_FILTERS:
        raise _error('ids', 'Could not read the new order.')
    own = {saved.pk: saved for saved in SavedFilter.objects.filter(user=user, pk__in=ids)}
    changed = []
    for index, pk in enumerate(ids):
        saved = own.get(pk)
        if saved is not None:
            saved.position = index
            changed.append(saved)
    with transaction.atomic():
        SavedFilter.objects.bulk_update(changed, ['position'])
    return len(changed)


# -- Counts and summaries ------------------------------------------------------------------------


def open_task_counts[K](user: User, params_by_key: Mapping[K, FilterParams]) -> dict[K, int | None]:
    """How many open tasks each of the `params_by_key` selects (completed ones are never
    counted), all in one query: a conditional `Count` per filter over the user's open tasks, under
    the search's one `statement_timeout`. A filter whose search can't run (an invalid pattern) is
    `None`; if the query itself times out (or the server rejects a pattern) every count is
    `None`."""
    counts: dict[K, int | None] = dict.fromkeys(params_by_key)
    aggregates = {}
    names: dict[str, K] = {}
    for key, params in params_by_key.items():
        try:
            condition = params.condition(user)
        except ValidationError:
            continue
        name = f'count_{len(names)}'
        names[name] = key
        aggregates[name] = Count('pk', filter=condition)
    if not aggregates:
        return counts
    open_tasks = Task.objects.filter(user=user, deleted_at__isnull=True, completed_at__isnull=True)
    try:
        with search_guard():
            totals = open_tasks.aggregate(**aggregates)
    except ValidationError:
        return counts
    for name, key in names.items():
        counts[key] = totals[name]
    return counts


def open_task_count(user: User, params: FilterParams) -> int | None:
    """`open_task_counts` for one filter."""
    return open_task_counts(user, {0: params})[0]


def _read_params(user: User, filters: Sequence[SavedFilter]) -> dict[UUID, FilterParams]:
    valid = label_ids(user)
    return {
        saved.pk: FilterParams.from_dict(saved.params, valid_label_ids=valid) for saved in filters
    }


def filter_counts(user: User, filters: Sequence[SavedFilter]) -> dict[UUID, int | None]:
    """`{filter id: open task count}` for `filters` (`None`: can't be counted), in one query."""
    return open_task_counts(user, _read_params(user, filters))


@dataclass(slots=True)
class FilterRow:
    """A saved filter as the filters page lists it: its params (read for this user) and a
    summary. The count of its open tasks comes later (`filter_counts`)."""

    filter: SavedFilter
    params: FilterParams
    summary: str
    dropped_labels: int = 0


def filter_rows(user: User, filters: Sequence[SavedFilter]) -> list[FilterRow]:
    """`FilterRow`s for `filters`; the user's labels are read once. No count is computed."""
    params = _read_params(user, filters)
    names = label_names(user)
    return [
        FilterRow(
            filter=saved,
            params=params[saved.pk],
            summary=describe_params(params[saved.pk], names),
            dropped_labels=len(
                set(FilterParams.from_dict(saved.params).labels) - set(params[saved.pk].labels)
            ),
        )
        for saved in filters
    ]


def sidebar_filters(user: User) -> tuple[list[SavedFilter], int]:
    """The filters the sidebar lists (at most `SIDEBAR_LIMIT`) and how many more there are."""
    filters = list(list_filters(user))
    return filters[:SIDEBAR_LIMIT], max(len(filters) - SIDEBAR_LIMIT, 0)
