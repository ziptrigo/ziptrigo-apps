"""Admin registrations for `core`'s own models (currently just the scheduler's job status)."""

from django.contrib import admin

from .admin_site import custom_admin_site
from .models import ScheduledJob


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


custom_admin_site.register(ScheduledJob, ScheduledJobAdmin)
