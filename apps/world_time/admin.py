"""Admin registrations for the World Time app: the settings singleton (superusers only) and
read-only `ModelAdmin`s for support (tabs with their locations, accounts, the city tables). Tabs
and locations are changed by their owner, through the services; the cities are reference data,
seeded from the file in the repository (`./manage.py world_time_seed_cities`), never edited here.
Deleting a *user* still removes their rows through the cascade
(`apps.accounts.admin.CASCADE_WITH_USER_APP_LABELS` lists `world_time`).
"""

from django.contrib import admin
from django.db.models import Count
from django.http import HttpRequest

from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .models import City, CityData, Location, Tab, WorldTimeAccount, WorldTimeSettings


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """No add, change or delete: everything is shown, nothing is edited."""

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
custom_admin_site.register(WorldTimeAccount, WorldTimeAccountAdmin)
custom_admin_site.register(City, CityAdmin)
custom_admin_site.register(CityData, CityDataAdmin)
