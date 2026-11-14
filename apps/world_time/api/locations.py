"""The location endpoints: the search, and a tab's rows (add, label, home, remove, reorder).

The search spends `WT_SEARCH_USER`; every change spends `WT_EDIT_USER`, after the checks that need
no write (which of `city` / `zone` was sent, the label's length).
"""

from uuid import UUID

from django.db import transaction
from ninja.errors import HttpError

from .. import services
from ..limits import SEARCH_QUERY_MAX
from ..schemas import (
    WorldTimeLocationCreateSchema,
    WorldTimeLocationSchema,
    WorldTimeLocationUpdateSchema,
    WorldTimeOrderSchema,
    WorldTimeSearchResultSchema,
)
from ..services import locations as location_service
from .common import charge, location_data, mapped_errors, owned_location, owned_tab
from .router import router


@router.get('/search', response=list[WorldTimeSearchResultSchema])
def search(request, q: str = ''):
    """Places matching `q` (at least 2 characters; longer than 60 is cut): a time zone
    abbreviation's zones first (`PST`, `IST`), then cities, most populous first (by name without
    accents, or by another name such as `Lisboa`), then time zones by name. Empty when nothing
    matches, and for cities while the city list has not been loaded (`GET /settings`:
    `cities_loaded`)."""
    charge(request, 'WT_SEARCH_USER')
    return [
        {
            'kind': result.kind,
            'value': result.value,
            'name': result.name,
            'detail': result.detail,
            'hint': result.hint,
        }
        for result in services.search(q[:SEARCH_QUERY_MAX])
    ]


@router.post('/tabs/{tab_id}/locations', response={201: WorldTimeLocationSchema})
def add_location(request, tab_id: UUID, payload: WorldTimeLocationCreateSchema):
    """Add a place to the end of a tab: `city` (a GeoNames id from the search) or `zone` (an IANA
    name), with an optional `label`. The first row of an empty tab becomes its home. A place
    already in the tab, a full tab, an unknown city or zone are 400s."""
    tab = owned_tab(request, tab_id)
    if (payload.city is None) == (not payload.zone):
        raise HttpError(400, 'Send either city or zone, not both.')
    with mapped_errors():
        location_service.clean_label(payload.label)  # a refusal here spends no budget
        charge(request, 'WT_EDIT_USER')
        with transaction.atomic():
            if payload.city is not None:
                location = services.add_city(tab, payload.city)
            else:
                location = services.add_zone(tab, payload.zone or '')
            if payload.label.strip():
                services.set_label(location, payload.label)
    return 201, location_data(location)


# Registered before the `{location_id}` routes: a path is matched in the order it was added.
@router.post('/tabs/{tab_id}/locations/reorder', response=list[WorldTimeLocationSchema])
def reorder_locations(request, tab_id: UUID, payload: WorldTimeOrderSchema):
    """Put the tab's locations in the order given and answer them all, in order. Ids that aren't
    the tab's are ignored; rows left out keep their place among the others."""
    tab = owned_tab(request, tab_id)
    charge(request, 'WT_EDIT_USER')
    services.reorder_locations(tab, payload.ids)
    return [location_data(row) for row in services.list_locations(tab)]


@router.patch('/tabs/{tab_id}/locations/{location_id}', response=WorldTimeLocationSchema)
def update_location(
    request, tab_id: UUID, location_id: UUID, payload: WorldTimeLocationUpdateSchema
):
    """Change a row: its `label`, and/or make it the tab's `home`. A body with nothing in it
    changes nothing and spends nothing."""
    location = owned_location(owned_tab(request, tab_id), location_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get('home') is False:
        raise HttpError(400, 'home: Make another location the home instead.')
    wants_home = changes.get('home') is True and not location.is_home
    if 'label' not in changes and not wants_home:
        return location_data(location)
    with mapped_errors():
        if 'label' in changes:
            location_service.clean_label(changes['label'])
        charge(request, 'WT_EDIT_USER')
        with transaction.atomic():
            if 'label' in changes:
                services.set_label(location, changes['label'] or '')
            if wants_home:
                services.set_home(location)
    return location_data(location)


@router.delete('/tabs/{tab_id}/locations/{location_id}', response={204: None})
def remove_location(request, tab_id: UUID, location_id: UUID):
    """Remove a row. If it was the home, the first remaining row becomes the home."""
    location = owned_location(owned_tab(request, tab_id), location_id)
    charge(request, 'WT_EDIT_USER')
    services.remove_location(location)
    return 204, None
