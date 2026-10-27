"""Admin registrations for Short URLs: the settings singleton (superusers only) and a read-only
list of links for support. A link is changed through the services (the owner's pages), never here:
editing a row directly would skip the code, billing and status rules."""

from django.contrib import admin
from django.http import HttpRequest

from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .models import ShortLink, ShortUrlSettings
from .services import display_state, short_url


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """No add, change or delete: everything is shown, nothing is edited."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class ShortUrlSettingsAdmin(SingletonSettingsAdmin):
    """Singleton settings page: there's only ever the one row `ShortUrlSettings.load()` creates."""

    fieldsets = [
        (
            'Prices (credits, one-off, per link)',
            {
                'fields': [
                    'custom_alias_price',
                    'password_price',
                    'analytics_price',
                    'extra_link_price',
                ],
                'description': 'A price of 0 makes that extra free.',
            },
        ),
        ('Allowances (per user)', {'fields': ['free_active_links', 'max_active_links']}),
        (
            'Analytics and abuse',
            {'fields': ['click_event_retention_days', 'auto_hold_report_threshold']},
        ),
    ]


class ShortLinkAdmin(ReadOnlyModelAdmin):
    list_display = [
        'code',
        'owner',
        'destination_preview',
        'status',
        'state',
        'click_count',
        'is_alias',
        'created_at',
    ]
    list_filter = ['status', 'is_alias', 'created_at']
    search_fields = ['code_key', 'destination', 'title', 'owner__email']
    list_select_related = ['owner']
    date_hierarchy = 'created_at'
    readonly_fields = ['public_link']

    @admin.display(description='Destination')
    def destination_preview(self, obj: ShortLink) -> str:
        return obj.destination if len(obj.destination) <= 60 else obj.destination[:57] + '...'

    @admin.display(description='Now')
    def state(self, obj: ShortLink) -> str:
        return display_state(obj)

    @admin.display(description='Short link')
    def public_link(self, obj: ShortLink) -> str:
        return short_url(obj.code)


custom_admin_site.register(ShortUrlSettings, ShortUrlSettingsAdmin)
custom_admin_site.register(ShortLink, ShortLinkAdmin)
