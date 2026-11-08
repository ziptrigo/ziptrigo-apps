"""Admin registrations for `core`'s own models: the scheduler's job status, the singleton
`CoreSettings` page (issue #58, mirrors `apps.file_transfer.admin.FileTransferSettingsAdmin`),
a read-only `EmailVerification` admin for support, a read-only `RateLimitCounter` admin
(issue #53 code review) for debugging which rules are actually tripping, and a read-only
`Notification` admin (issue #169), and read-only push subscriptions and answers (issue #239).
"""

from urllib.parse import urlsplit

from django.contrib import admin, messages

from .admin_singleton import SingletonSettingsAdmin
from .admin_site import custom_admin_site
from .models import (
    CoreSettings,
    EmailVerification,
    Notification,
    PushAction,
    PushSubscription,
    RateLimitCounter,
    ScheduledJob,
)
from .services import push

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
    """Read-only: job status is written by the scheduler runner, never edited by hand. The change
    page shows the job's `JobSpec.description` above the fields
    (`admin/core/scheduledjob/change_form.html`)."""

    change_form_template = 'admin/core/scheduledjob/change_form.html'

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

    def change_view(self, request, object_id, form_url='', extra_context=None):
        from .scheduler import get_job

        obj = self.get_object(request, object_id)
        job = get_job(obj.name) if obj is not None else None
        extra_context = {
            **(extra_context or {}),
            'job_registered': job is not None,
            'job_description': job.description if job is not None else '',
        }
        return super().change_view(request, object_id, form_url, extra_context)

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


class PushSubscriptionAdmin(admin.ModelAdmin):
    """Read-only (issue #239): a browser subscribes and unsubscribes itself, and a gone one is
    deleted when the push service says so. The endpoint is a capability URL (anyone holding it
    and the keys can push to that browser), so only its push service's host is shown.

    "Send test notification" queues the Yes / No test for the users of the selected subscriptions
    (every device of theirs), superusers only: it reaches a person's phone."""

    list_display = ['user', 'push_service', 'user_agent', 'created_at', 'last_success_at']
    list_filter = ['created_at', 'last_success_at']
    search_fields = ['user__email', 'user__name']
    list_select_related = ['user']
    ordering = ['-created_at']
    fields = ['id', 'user', 'push_service', 'user_agent', 'created_at', 'last_success_at']
    readonly_fields = fields
    actions = ['send_test_notification']

    @admin.display(description='Push service')
    def push_service(self, obj: PushSubscription) -> str:
        return urlsplit(obj.endpoint).hostname or ''

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def has_send_test_permission(self, request) -> bool:
        return request.user.is_superuser

    @admin.action(description='Send test notification', permissions=['send_test'])
    def send_test_notification(self, request, queryset):
        if not push.is_configured():
            self.message_user(
                request, 'Push is not configured (VAPID_* settings).', level=messages.ERROR
            )
            return
        users = {subscription.user for subscription in queryset.select_related('user')}
        # Queued, not sent here: each device is a call to its push service, and a request that
        # waits on many of them can outlast the worker timeout.
        for user in users:
            push.queue_test_push(user)
        self.message_user(
            request,
            f'Test notification queued for {len(users)} user(s), every device of theirs. The '
            'push answers list shows the replies.',
            level=messages.SUCCESS,
        )


class PushActionAdmin(admin.ModelAdmin):
    """Read-only (issue #239): the answers users gave with a notification's buttons."""

    list_display = ['created_at', 'user', 'kind', 'action']
    list_filter = ['kind', 'action', 'created_at']
    search_fields = ['user__email', 'user__name', 'kind']
    list_select_related = ['user']
    ordering = ['-created_at']
    fields = ['id', 'user', 'push_id', 'kind', 'action', 'created_at']
    readonly_fields = fields

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
custom_admin_site.register(PushSubscription, PushSubscriptionAdmin)
custom_admin_site.register(PushAction, PushActionAdmin)
