"""`GET /api/time/settings`: the caps and how much of them the caller uses."""

from ..models import Event, Tab, WorldTimeSettings
from ..schemas import WorldTimeSettingsSchema
from ..services import is_seeded
from .router import router


@router.get('/settings', response=WorldTimeSettingsSchema)
def settings(request):
    """The site's caps for World Time (tabs, locations per tab, shared events, and how many days
    after it ends an event is deleted), how many tabs and events the caller has, and whether the
    city list is loaded (while it is not, the search only finds time zones). World Time is free."""
    config = WorldTimeSettings.load()
    return {
        'max_tabs': config.max_tabs_per_user,
        'tabs': Tab.objects.filter(user=request.auth).count(),
        'max_locations_per_tab': config.max_locations_per_tab,
        'max_events': config.max_events_per_user,
        'events': Event.objects.filter(user=request.auth).count(),
        'event_purge_days': config.event_purge_days,
        'cities_loaded': is_seeded(),
    }
