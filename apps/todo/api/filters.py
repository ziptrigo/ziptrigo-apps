"""The saved-filter endpoints (`/api/todo/filters`): a named search (text, priorities, labels, a date
range and a sort) that can be run again. Each calls the service the Filters page uses; changes
spend `TODO_EDIT_USER` and running one spends `TODO_SEARCH_USER`, like any search.
"""

from contextlib import nullcontext
from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from .. import services
from ..models import SavedFilter
from ..schemas import (
    FilterCreateSchema,
    FilterOrderSchema,
    FilterSchema,
    FilterUpdateSchema,
    TaskListSchema,
)
from ..services import saved_filters
from ..services.filters import filter_tasks, label_names
from ..services.saved_filters import stored_params
from ..services.search import search_guard
from .common import (
    DEFAULT_LIMIT,
    charge,
    clamp_page,
    context_for,
    decorated,
    mapped_errors,
    task_data,
)
from .router import router


def _owned(request, filter_id: UUID) -> SavedFilter:
    """The caller's saved filter, or a 404."""
    return get_object_or_404(
        SavedFilter.objects.select_related('user'), pk=filter_id, user=request.auth
    )


def _data(saved: SavedFilter, user, names: dict[str, str]) -> dict:
    params = stored_params(saved, user)
    return {
        'id': saved.pk,
        'name': saved.name,
        'position': saved.position,
        'params': params.to_dict(),
        'summary': services.describe_params(params, names),
        'created_at': saved.created_at,
    }


@router.get('/filters', response=list[FilterSchema])
def list_filters(request):
    """The caller's saved filters in order. A label deleted since a filter was saved is dropped
    from its `params`."""
    names = label_names(request.auth)
    return [_data(saved, request.auth, names) for saved in services.list_filters(request.auth)]


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
    """Rename a filter and/or replace its search (`params`, as a whole). Both are saved, or
    neither. Neither can be cleared: an explicit `null` is a 400 (to search for everything, send
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
            if payload.params is not None:
                saved_filters.clean_params(request.auth, payload.params.to_params())
            charge(request, 'TODO_EDIT_USER')
            with transaction.atomic():
                if 'name' in changes:
                    services.rename_filter(saved, changes['name'])
                if payload.params is not None:
                    services.update_filter_params(saved, payload.params.to_params())
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
    the total. Spends `TODO_SEARCH_USER`."""
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
    context = context_for(request)
    return {
        'count': count,
        'limit': limit,
        'offset': offset,
        'results': [task_data(task, context) for task in rows],
    }
