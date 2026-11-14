"""Admin registrations for the World Time app: the settings singleton (superusers only) and
read-only `ModelAdmin`s for support (tabs with their locations, accounts, the city tables). Tabs
and locations are changed by their owner, through the services; the cities are reference data,
seeded from the file in the repository (`./manage.py world_time_seed_cities`), never edited here.
Deleting a *user* still removes their rows through the cascade
(`apps.accounts.admin.CASCADE_WITH_USER_APP_LABELS` lists `world_time`).
"""

from django.contrib import admin
from django.db.models import Count
from django.db.models.constants import LOOKUP_SEP
from django.http import HttpRequest

from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .models import City, CityData, Event, Location, Tab, WorldTimeAccount, WorldTimeSettings


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """No add, change or delete: everything is shown, nothing is edited.

    **No lookup may reach an event's code** (`lookup_allowed`): Django accepts any lookup on a
    model's own fields from the query string, so `?code__startswith=Ab` would narrow a full code
    down one character at a time, however little the page shows of it (the Notes admins' rule)."""

    def lookup_allowed(self, lookup: str, value, request: HttpRequest | None = None) -> bool:
        if 'code' in lookup.split(LOOKUP_SEP):
            return False
        return super().lookup_allowed(lookup, value, request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class WorldTimeSettingsAdmin(SingletonSettingsAdmin):
    """Singleton settings page: there's only ever the one row `WorldTimeSettings.load()` creates."""


class LocationInline(admin.TabularInline):
    model = Location
    extra = 0
    can_delete = False
    fields = ('name', 'label', 'region', 'country', 'timezone', 'is_home', 'position')
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class TabAdmin(ReadOnlyModelAdmin):
    list_display = ('name', 'user', 'location_count', 'position', 'created_at')
    search_fields = ('name', 'user__email')
    list_select_related = ('user',)
    date_hierarchy = 'created_at'
    inlines = (LocationInline,)

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).annotate(_locations=Count('locations'))

    @admin.display(description='Locations', ordering='_locations')
    def location_count(self, obj) -> int:
        return obj._locations


class EventAdmin(ReadOnlyModelAdmin):
    """Shared events. The code is the capability that opens the public page, so only its first
    characters are shown and it is in no search."""

    list_display = ('title', 'user', 'starts_at', 'ends_at', 'code_start', 'created_at')
    search_fields = ('title', 'user__email')
    list_select_related = ('user',)
    date_hierarchy = 'starts_at'
    fields = (
        'title',
        'user',
        'starts_at',
        'ends_at',
        'code_start',
        'locations',
        'created_at',
        'updated_at',
    )

    @admin.display(description='Code')
    def code_start(self, obj) -> str:
        return f'{obj.code[:6]}...'


class WorldTimeAccountAdmin(ReadOnlyModelAdmin):
    list_display = ('user', 'created_at')
    search_fields = ('user__email',)
    list_select_related = ('user',)


class CityAdmin(ReadOnlyModelAdmin):
    list_display = ('name', 'region', 'country', 'timezone', 'population', 'geoname_id')
    search_fields = ('name', 'search_name', 'country', 'timezone')
    list_filter = ('country_code',)
    ordering = ('-population',)
    show_full_result_count = False


class CityDataAdmin(ReadOnlyModelAdmin):
    """When the city tables were last seeded, and from which file (its SHA-256)."""

    list_display = ('__str__', 'name_count', 'seeded_at', 'sha256')


custom_admin_site.register(WorldTimeSettings, WorldTimeSettingsAdmin)
custom_admin_site.register(Tab, TabAdmin)
custom_admin_site.register(Event, EventAdmin)
custom_admin_site.register(WorldTimeAccount, WorldTimeAccountAdmin)
custom_admin_site.register(City, CityAdmin)
custom_admin_site.register(CityData, CityDataAdmin)
