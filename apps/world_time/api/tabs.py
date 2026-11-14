"""The tab endpoints (`/api/time/tabs`): named sets of locations. Each calls the service the web
page uses; every change spends `WT_EDIT_USER`, after the body's own checks."""

from uuid import UUID

from django.db.models import Count

from .. import services
from ..models import Tab
from ..schemas import WorldTimeTabDetailSchema, WorldTimeTabSchema, WorldTimeTabWriteSchema
from ..services import tabs as tab_service
from .common import charge, location_data, mapped_errors, owned_tab
from .router import router


def tab_detail(tab: Tab) -> dict:
    return {
        'id': tab.pk,
        'name': tab.name,
        'position': tab.position,
        'locations': [location_data(row) for row in services.list_locations(tab)],
    }


@router.get('/tabs', response=list[WorldTimeTabSchema])
def list_tabs(request):
    """The caller's tabs in order. A caller who has none yet gets one, `My times`, whose only row
    is the account's time zone (UTC when it is not set), as on the website."""
    services.list_tabs(request.auth)  # makes the first tab on a first call
    tabs = (
        Tab.objects.filter(user=request.auth)
        .annotate(location_count=Count('locations'))
        .order_by('position', 'created_at')
    )
    return list(tabs)


@router.post('/tabs', response={201: WorldTimeTabDetailSchema})
def create_tab(request, payload: WorldTimeTabWriteSchema):
    """Add a tab at the end. It starts with one row, the account's time zone, as its home."""
    with mapped_errors():
        tab_service.clean_name(payload.name)  # a refusal here spends no budget
        charge(request, 'WT_EDIT_USER')
        tab = services.create_tab(request.auth, payload.name)
    return 201, tab_detail(tab)


@router.get('/tabs/{tab_id}', response=WorldTimeTabDetailSchema)
def get_tab(request, tab_id: UUID):
    """A tab with its locations in order."""
    return tab_detail(owned_tab(request, tab_id))


@router.patch('/tabs/{tab_id}', response=WorldTimeTabDetailSchema)
def rename_tab(request, tab_id: UUID, payload: WorldTimeTabWriteSchema):
    """Rename a tab."""
    tab = owned_tab(request, tab_id)
    with mapped_errors():
        tab_service.clean_name(payload.name)
        charge(request, 'WT_EDIT_USER')
        services.rename_tab(tab, payload.name)
    return tab_detail(tab)


@router.delete('/tabs/{tab_id}', response={204: None})
def delete_tab(request, tab_id: UUID):
    """Delete a tab and its locations. The last tab can't be deleted (400)."""
    tab = owned_tab(request, tab_id)
    with mapped_errors():
        tab_service.check_deletable(tab)  # a refusal here spends no budget
        charge(request, 'WT_EDIT_USER')
        services.delete_tab(tab)
    return 204, None
