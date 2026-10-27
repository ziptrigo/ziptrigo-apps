"""Admin registrations for `core`'s own models: the scheduler's job status, the singleton
`CoreSettings` page (issue #58, mirrors `apps.file_transfer.admin.FileTransferSettingsAdmin`),
a read-only `EmailVerification` admin for support, a read-only `RateLimitCounter` admin
(issue #53 code review) for debugging which rules are actually tripping, and a read-only
`Notification` admin (issue #169).
"""

from django.contrib import admin

from .admin_singleton import SingletonSettingsAdmin
from .admin_site import custom_admin_site
from .models import CoreSettings, EmailVerification, Notification, RateLimitCounter, ScheduledJob

#: Every field on `EmailVerification` except its code/token hashes -- spelled out rather than
#: introspected from `EmailVerification._meta` (`ty` has no insight into that metaclass-populated
#: attribute; see the class-level comment in `apps/qr_code/models/qrcode.py`).
_EMAIL_VERIFICATION_FIELDS = [
    'id',
    'email',
    'purpose',
    'attempts',
    'confirmed_at',
    'invalidated_at',
    'expires_at',
    'created_at',
]


class ScheduledJobAdmin(admin.ModelAdmin):
    """Read-only: job status is written by the scheduler runner, never edited by hand."""

    list_display = [
        'name',
        'interval_seconds',
        'last_started_at',
        'last_finished_at',
        'last_success_at',
        'run_count',
        'locked_until',
    ]
    readonly_fields = [
        'name',
        'interval_seconds',
        'locked_until',
        'last_started_at',
        'last_finished_at',
        'last_success_at',
        'last_error',
        'run_count',
    ]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class CoreSettingsAdmin(SingletonSettingsAdmin):
    """Singleton settings page (issue #58): there's only ever the one row `CoreSettings.load()`
    creates."""


class EmailVerificationAdmin(admin.ModelAdmin):
    """Read-only: support can look verifications up, but the service layer
    (`apps.core.services.email_verification`) is the only sanctioned way to change one."""

    list_display = [
        'id',
        'email',
        'purpose',
        'attempts',
        'confirmed_at',
        'expires_at',
        'created_at',
    ]
    list_filter = ['purpose', 'created_at']
    search_fields = ['id', 'email']
    fields = _EMAIL_VERIFICATION_FIELDS
    readonly_fields = _EMAIL_VERIFICATION_FIELDS

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class RateLimitCounterAdmin(admin.ModelAdmin):
    """Read-only: rows are written only by `apps.core.ratelimit`'s atomic upsert and cleaned up
    only by the `purge_expired_rate_limit_counters` scheduler job. `key` is already a hash (see
    the model docstring) -- nothing here identifies which account, email, IP or transfer a row
    belongs to, only which *rule* and roughly how close to its limit it is."""

    list_display = ['key', 'count', 'expires_at']
    list_filter = ['expires_at']
    search_fields = ['key']
    readonly_fields = ['key', 'count', 'expires_at']

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class ReadStateFilter(admin.SimpleListFilter):
    """New (never read) or read."""

    title = 'read state'
    parameter_name = 'read'

    def lookups(self, request, model_admin):
        return [('no', 'New (unread)'), ('yes', 'Read')]

    def queryset(self, request, queryset):
        if self.value() == 'no':
            return queryset.filter(read_at__isnull=True)
        if self.value() == 'yes':
            return queryset.filter(read_at__isnull=False)
        return queryset


class DismissedStateFilter(admin.SimpleListFilter):
    """Dismissed by the user or still listed."""

    title = 'dismissed'
    parameter_name = 'dismissed'

    def lookups(self, request, model_admin):
        return [('no', 'Not dismissed'), ('yes', 'Dismissed')]

    def queryset(self, request, queryset):
        if self.value() == 'no':
            return queryset.filter(dismissed_at__isnull=True)
        if self.value() == 'yes':
            return queryset.filter(dismissed_at__isnull=False)
        return queryset


class NotificationAdmin(admin.ModelAdmin):
    """Read-only (issue #169): notifications are created by products through
    `apps.core.services.notifications.notify` and changed only by their recipient. Support can
    look one up by who it was for. Deleting a user still removes theirs (the foreign key cascades;
    `accounts`' `UserAdmin` accounts for that)."""

    list_display = ['created_at', 'user', 'source', 'title', 'read_at', 'opened_at', 'dismissed_at']
    list_filter = ['source', ReadStateFilter, DismissedStateFilter, 'created_at']
    search_fields = ['user__email', 'user__name', 'title']
    list_select_related = ['user']
    ordering = ['-created_at']
    readonly_fields = [
        'id',
        'user',
        'source',
        'title',
        'body',
        'url',
        'created_at',
        'read_at',
        'opened_at',
        'dismissed_at',
    ]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


custom_admin_site.register(ScheduledJob, ScheduledJobAdmin)
custom_admin_site.register(CoreSettings, CoreSettingsAdmin)
custom_admin_site.register(EmailVerification, EmailVerificationAdmin)
custom_admin_site.register(RateLimitCounter, RateLimitCounterAdmin)
custom_admin_site.register(Notification, NotificationAdmin)
