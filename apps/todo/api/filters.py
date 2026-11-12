"""The saved-filter endpoints (`/api/todo/filters`): a named search (text, priorities, labels, a date
range and a sort) that can be run again. Each calls the service the Filters page uses; changes
spend `TODO_EDIT_USER` and running one spends `TODO_SEARCH_USER`, like any search.
"""

from contextlib import nullcontext
from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.core import ratelimit
from apps.core.datetime_format import PUBLIC

from .. import services
from ..models import SavedFilter
from ..schemas import (
    FilterCreateSchema,
    FilterOrderSchema,
    FilterParamsSchema,
    FilterSchema,
    FilterUpdateSchema,
    TaskListSchema,
)
from ..services import saved_filters
from ..services.filters import FilterParams, filter_tasks, label_names
from ..services.saved_filters import filter_counts, stored_params
from ..services.search import attach_matches, prepare_query, search_guard
from .common import (
    DEFAULT_LIMIT,
    charge,
    clamp_page,
    context_for,
    decorated,
    mapped_errors,
    search_fields,
    task_data,
)
from .router import router


def _owned(request, filter_id: UUID) -> SavedFilter:
    """The caller's saved filter, or a 404."""
    return get_object_or_404(
        SavedFilter.objects.select_related('user'), pk=filter_id, user=request.auth
    )


def _data(saved: SavedFilter, user, names: dict[str, str]) -> dict:
    """A saved filter as `FilterSchema`: what it selects (`states` and the resolution of closed
    tasks included, whether it was saved here or on the website) and its summary in the public
    wording."""
    valid = set(names)  # the user's label ids, loaded once by the caller
    params = FilterParams.from_dict(saved.params, valid_label_ids=valid)
    return {
        'id': saved.pk,
        'name': saved.name,
        'position': saved.position,
        'params': FilterParamsSchema.from_stored(params),
        'summary': services.describe_params(params, names, PUBLIC),
        'created_at': saved.created_at,
        'dropped_labels': len(set(FilterParams.from_dict(saved.params).labels) - valid),
    }


@router.get('/filters', response=list[FilterSchema])
def list_filters(request):
    """The caller's saved filters in order. A label deleted since a filter was saved is dropped
    from its `params`."""
    names = label_names(request.auth)
    return [_data(saved, request.auth, names) for saved in services.list_filters(request.auth)]


# Registered before the `{filter_id}` routes, like `reorder`: a path is matched in the order added.
@router.get('/filters/counts', response=dict[str, int | None])
def filter_open_counts(request):
    """How many open tasks each of the caller's saved filters selects, as `{filter id: count}`, for
    all of them in **one** query and one hit of the `TODO_SEARCH_USER` budget (what the website's
    sidebar fetches). A count is `null` when the filter's search can't be run (an invalid
    pattern); when the query takes too long, or the budget is spent, **every** count is `null`:
    never an error. A user with no filters gets `{}` and spends nothing."""
    filters = list(services.list_filters(request.auth))
    if not filters:
        return {}
    if not ratelimit.hit_user(request.auth, 'TODO_SEARCH_USER').allowed:
        return {str(saved.pk): None for saved in filters}
    counts = filter_counts(request.auth, filters)
    return {str(saved.pk): counts.get(saved.pk) for saved in filters}


@router.post('/filters', response={201: FilterSchema})
def create_filter(request, payload: FilterCreateSchema):
    """Save a search under a name (unique, ignoring case; at most 50 filters). A search text that
    can't run (empty after cleaning, too long, an invalid pattern) is a 400, before the edit budget
    is spent."""
    with mapped_errors():
        saved_filters.clean_name(payload.name)
        saved_filters.clean_params(request.auth, payload.params.to_params())
        charge(request, 'TODO_EDIT_USER')
        saved = services.save_filter(request.auth, payload.name, payload.params.to_params())
    return 201, _data(saved, request.auth, label_names(request.auth))


# Registered before the `{filter_id}` routes (a different method and a `uuid` converter, so
# `reorder` is never read as an id either way, but it reads better next to the others).
@router.post('/filters/reorder', response=list[FilterSchema])
def reorder_filters(request, payload: FilterOrderSchema):
    """Put the saved filters in the order given (ids that aren't yours are ignored; at most 50, a
    longer list is a 422)."""
    charge(request, 'TODO_EDIT_USER')
    with mapped_errors():
        services.reorder_filters(request.auth, payload.ids)
    return list_filters(request)


@router.patch('/filters/{filter_id}', response=FilterSchema)
def update_filter(request, filter_id: UUID, payload: FilterUpdateSchema):
    """Rename a filter and/or replace its search (`params`, as a whole: criteria left out take
    their defaults, as on the website). Both are saved, or neither. Neither can be cleared: an explicit `null` is a 400 (to search for everything, send
    the default `params`). A body with nothing in it changes nothing and spends no budget; a value
    that can't be used is refused before the edit budget is spent."""
    saved = _owned(request, filter_id)
    changes = payload.model_dump(exclude_unset=True)
    for field in changes:
        if changes[field] is None:
            raise HttpError(400, f'{field}: Cannot be null.')
    if changes:
        with mapped_errors():
            if 'name' in changes:
                saved_filters.clean_name(changes['name'])
            new_params = payload.params.to_params() if payload.params is not None else None
            if new_params is not None:
                saved_filters.clean_params(request.auth, new_params)
            charge(request, 'TODO_EDIT_USER')
            with transaction.atomic():
                if 'name' in changes:
                    services.rename_filter(saved, changes['name'])
                if new_params is not None:
                    services.update_filter_params(saved, new_params)
    return _data(saved, request.auth, label_names(request.auth))


@router.delete('/filters/{filter_id}', response={204: None})
def delete_filter(request, filter_id: UUID):
    saved = _owned(request, filter_id)
    charge(request, 'TODO_EDIT_USER')
    services.delete_filter(saved)
    return 204, None


@router.get('/filters/{filter_id}/tasks', response=TaskListSchema)
def run_filter(request, filter_id: UUID, limit: int = DEFAULT_LIMIT, offset: int = 0):
    """Run a saved filter: the tasks it selects, in its sort order (completed ones last), as
    `GET /tasks` lists them. `limit` (1-100, default 50) and `offset` page the result; `count` is
    the total. Spends `TODO_SEARCH_USER`. When the filter has search text each task carries the
    snippet fields of `GET /tasks`; without it they are `null`."""
    saved = _owned(request, filter_id)
    user = request.auth
    limit, offset = clamp_page(limit, offset)
    params = stored_params(saved, user)
    charge(request, 'TODO_SEARCH_USER')
    with mapped_errors():
        queryset = filter_tasks(user, params)  # validates the search text
        with search_guard() if params.q.strip() else nullcontext():
            count = queryset.count()
            rows = list(decorated(queryset)[offset : offset + limit])
        searched = bool(params.q.strip())
        if searched:
            attach_matches(rows, prepare_query(params.q, params.mode))
    context = context_for(request)
    return {
        'count': count,
        'limit': limit,
        'offset': offset,
        'results': [
            {**task_data(task, context), **(search_fields(task) if searched else {})}
            for task in rows
        ],
    }
