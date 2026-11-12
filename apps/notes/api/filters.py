"""The saved-filter endpoints (`/api/notes/filters`): a named search (text, scope, tags, shared, an
updated range and a sort) that can be run again. Each calls the service the Filters page uses;
changes spend `NOTES_EDIT_USER` and running one spends `NOTES_SEARCH_USER`, like any search.
"""

from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.core.datetime_format import PUBLIC

from .. import services
from ..models import SavedFilter
from ..schemas import (
    NoteFilterCreateSchema,
    NoteFilterOrderSchema,
    NoteFilterParamsSchema,
    NoteFilterSchema,
    NoteFilterUpdateSchema,
    NoteListSchema,
)
from ..services import saved_filters
from ..services.filters import tag_names
from ..services.saved_filters import stored_params
from ..services.search import order_results, prepare_query
from .common import DEFAULT_LIMIT, charge, clamp_page, mapped_errors, note_page
from .router import router


def _owned(request, filter_id: UUID) -> SavedFilter:
    """The caller's saved filter, or a 404."""
    return get_object_or_404(
        SavedFilter.objects.select_related('user'), pk=filter_id, user=request.auth
    )


def _data(saved: SavedFilter, user, names: dict[str, str]) -> dict:
    """A saved filter as `NoteFilterSchema`: what it selects and its summary in the public wording."""
    params = stored_params(saved, user)
    return {
        'id': saved.pk,
        'name': saved.name,
        'position': saved.position,
        'params': NoteFilterParamsSchema.from_stored(params),
        'summary': services.describe_params(params, names, PUBLIC),
        'created_at': saved.created_at,
    }


@router.get('/filters', response=list[NoteFilterSchema])
def list_filters(request):
    """The caller's saved filters in order. A tag deleted since a filter was saved is dropped from
    its `params`."""
    names = tag_names(request.auth)
    return [_data(saved, request.auth, names) for saved in services.list_filters(request.auth)]


@router.post('/filters', response={201: NoteFilterSchema})
def create_filter(request, payload: NoteFilterCreateSchema):
    """Save a search under a name (unique, ignoring case; at most 50 filters). A search text that
    can't run (empty after cleaning, too long, an invalid pattern) is a 400, before the edit budget
    is spent."""
    with mapped_errors():
        saved_filters.clean_name(payload.name)
        saved_filters.clean_params(request.auth, payload.params.to_params())
        charge(request, 'NOTES_EDIT_USER')
        saved = services.save_filter(request.auth, payload.name, payload.params.to_params())
    return 201, _data(saved, request.auth, tag_names(request.auth))


# Registered before the `{filter_id}` routes (a different method and a `uuid` converter, so
# `reorder` is never read as an id either way, but it reads better next to the others).
@router.post('/filters/reorder', response=list[NoteFilterSchema])
def reorder_filters(request, payload: NoteFilterOrderSchema):
    """Put the saved filters in the order given (ids that aren't yours are ignored; at most 50, a
    longer list is a 422)."""
    charge(request, 'NOTES_EDIT_USER')
    with mapped_errors():
        services.reorder_filters(request.auth, payload.ids)
    return list_filters(request)


@router.patch('/filters/{filter_id}', response=NoteFilterSchema)
def update_filter(request, filter_id: UUID, payload: NoteFilterUpdateSchema):
    """Rename a filter and/or replace its search (`params`, as a whole: criteria left out take
    their defaults, as on the website). Both are saved, or neither. Neither can be cleared: an
    explicit `null` is a 400 (to search for everything, send the default `params`). A body with
    nothing in it changes nothing and spends no budget; a value that can't be used is refused
    before the edit budget is spent."""
    saved = _owned(request, filter_id)
    changes = payload.model_dump(exclude_unset=True)
    for name in changes:
        if changes[name] is None:
            raise HttpError(400, f'{name}: Cannot be null.')
    if changes:
        with mapped_errors():
            if 'name' in changes:
                saved_filters.clean_name(changes['name'])
            new_params = payload.params.to_params() if payload.params is not None else None
            if new_params is not None:
                saved_filters.clean_params(request.auth, new_params)
            charge(request, 'NOTES_EDIT_USER')
            with transaction.atomic():
                if 'name' in changes:
                    services.rename_filter(saved, changes['name'])
                if new_params is not None:
                    services.update_filter_params(saved, new_params)
    return _data(saved, request.auth, tag_names(request.auth))


@router.delete('/filters/{filter_id}', response={204: None})
def delete_filter(request, filter_id: UUID):
    saved = _owned(request, filter_id)
    charge(request, 'NOTES_EDIT_USER')
    services.delete_filter(saved)
    return 204, None


@router.get('/filters/{filter_id}/notes', response=NoteListSchema)
def run_filter(request, filter_id: UUID, limit: int = DEFAULT_LIMIT, offset: int = 0):
    """Run a saved filter: the notes it selects (in its scope, never the Trash), in its sort
    order, as `GET /notes` lists them. `limit` (1-100, default 50) and `offset` page the result;
    `count` is the total. Spends `NOTES_SEARCH_USER` (nothing when its search text can't run: that
    is a 400). When the filter has search text each note carries the snippet fields of
    `GET /notes`; without it they are `null`."""
    saved = _owned(request, filter_id)
    user = request.auth
    limit, offset = clamp_page(limit, offset)
    params = stored_params(saved, user)
    with mapped_errors():
        # Validated before the budget is spent: a saved search that can't run costs nothing.
        query = prepare_query(params.q, params.mode) if params.q.strip() else None
        charge(request, 'NOTES_SEARCH_USER')
        queryset = services.filter_notes(user, params, ordered=False)
        ordered = order_results(queryset, params.sort, params.dir or None)
        return note_page(request, queryset, ordered, query=query, limit=limit, offset=offset)
