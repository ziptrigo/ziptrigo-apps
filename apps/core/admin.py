"""Admin registrations for `core`'s own models: the scheduler's job status, the singleton
`CoreSettings` page (issue #58, mirrors `apps.file_transfer.admin.FileTransferSettingsAdmin`),
a read-only `EmailVerification` admin for support, and a read-only `RateLimitCounter` admin
(issue #53 code review) for debugging which rules are actually tripping.
"""

from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import redirect

from .admin_site import AuthenticatedHttpRequest, custom_admin_site
from .models import CoreSettings, EmailVerification, RateLimitCounter, ScheduledJob

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


class CoreSettingsAdmin(admin.ModelAdmin):
    """Singleton settings page (issue #58): superusers only, and there's only ever the one row
    `CoreSettings.load()` creates."""

    def _is_superuser(self, request: AuthenticatedHttpRequest) -> bool:
        # `request.user` is typed `PermissionsMixin` here (see `AuthenticatedHttpRequest`'s
        # docstring in `admin_site.py`) -- `core` can't import `accounts`, so unlike
        # `apps.file_transfer.admin.FileTransferSettingsAdmin`'s equivalent check, there's no
        # `is_active` on that type to check; the admin site itself already requires an active,
        # staff-authenticated session to reach any view here at all.
        return bool(request.user.is_superuser)

    def has_module_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return self._is_superuser(request)

    def has_view_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return self._is_superuser(request)

    def has_add_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return self._is_superuser(request) and not CoreSettings.objects.exists()

    def has_change_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return self._is_superuser(request)

    def has_delete_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return False

    def changelist_view(
        self, request: AuthenticatedHttpRequest, extra_context=None
    ) -> HttpResponse:
        """Skip the changelist entirely -- there's only ever one row."""
        settings_row = CoreSettings.load()
        return redirect('custom_admin:core_coresettings_change', settings_row.pk)


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


custom_admin_site.register(ScheduledJob, ScheduledJobAdmin)
custom_admin_site.register(CoreSettings, CoreSettingsAdmin)
custom_admin_site.register(EmailVerification, EmailVerificationAdmin)
custom_admin_site.register(RateLimitCounter, RateLimitCounterAdmin)
