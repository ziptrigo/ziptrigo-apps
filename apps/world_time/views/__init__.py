from .events import event_create, event_delete, event_edit, event_page, events_page
from .locations import (
    location_add,
    location_home,
    location_label,
    location_remove,
    location_reorder,
    location_search,
)
from .public import event_public
from .tabs import home_page, tab_create, tab_delete, tab_page, tab_rename

__all__ = [
    'event_create',
    'event_delete',
    'event_edit',
    'event_page',
    'event_public',
    'events_page',
    'home_page',
    'location_add',
    'location_home',
    'location_label',
    'location_remove',
    'location_reorder',
    'location_search',
    'tab_create',
    'tab_delete',
    'tab_page',
    'tab_rename',
]
