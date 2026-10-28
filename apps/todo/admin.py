"""Admin registrations for the To-do app: the settings singleton (superusers only) and read-only
`ModelAdmin`s for support -- tasks, accounts, labels. A task is changed through the services (the
user's own pages), never here: editing a row directly would skip numbering, billing and the Trash
rules. Deleting a *user* still removes their rows through the cascade
(`apps.accounts.admin.CASCADE_WITH_USER_APP_LABELS` lists `todo`).
"""

from django.contrib import admin, messages
from django.db.models import Count, Q
from django.http import HttpRequest

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .models import (
    Attachment,
    Comment,
    CommentRevision,
    InboundEmail,
    InboundMessage,
    InboxCode,
    Label,
    Recurrence,
    Reminder,
    ReminderDelivery,
    SavedFilter,
    Task,
    TodoAccount,
    TodoSettings,
)
from .services.billing import month_key
from .services.inbound import reset_message_for_retry


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """No add, change or delete: everything is shown, nothing is edited."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class TodoSettingsAdmin(SingletonSettingsAdmin):
    """Singleton settings page: there's only ever the one row `TodoSettings.load()` creates."""


class CompletedFilter(admin.SimpleListFilter):
    title = 'completed'
    parameter_name = 'completed'

    def lookups(self, request, model_admin):
        return [('yes', 'Completed'), ('no', 'Open')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(completed_at__isnull=False)
        if self.value() == 'no':
            return queryset.filter(completed_at__isnull=True)
        return queryset


class TrashedFilter(admin.SimpleListFilter):
    title = 'trash'
    parameter_name = 'trashed'

    def lookups(self, request, model_admin):
        return [('yes', 'In the trash'), ('no', 'Not in the trash')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(deleted_at__isnull=False)
        if self.value() == 'no':
            return queryset.filter(deleted_at__isnull=True)
        return queryset


class TaskAdmin(ReadOnlyModelAdmin):
    list_display = [
        'user',
        'number',
        'title',
        'due_date',
        'priority',
        'recurrence',
        'charged_credits',
        'unpaid',
        'source',
        'created_at',
    ]
    list_filter = [
        'priority',
        'source',
        CompletedFilter,
        TrashedFilter,
        ('unpaid_at', admin.EmptyFieldListFilter),
        'created_at',
    ]
    search_fields = ['title', 'number', 'user__email']
    list_select_related = ['user', 'recurrence']
    date_hierarchy = 'created_at'

    @admin.display(boolean=True, description='Unpaid', ordering='unpaid_at')
    def unpaid(self, obj: Task) -> bool:
        return obj.is_unpaid


class RecurrenceAdmin(ReadOnlyModelAdmin):
    list_display = [
        'user',
        'rrule',
        'mode',
        'anchor_date',
        'occurrences_created',
        'ended_at',
        'created_at',
    ]
    list_filter = ['mode', ('ended_at', admin.EmptyFieldListFilter)]
    search_fields = ['rrule', 'user__email']
    list_select_related = ['user']


class InboxCodeInline(admin.TabularInline):
    """The account's `todo+<code>@...` addresses, the retired ones included. Read-only."""

    model = InboxCode
    extra = 0
    can_delete = False
    fields = ['code', 'active', 'created_at', 'retired_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class TodoAccountAdmin(ReadOnlyModelAdmin):
    list_display = [
        'user',
        'next_task_number',
        'month_usage',
        'blocked',
        'stored_bytes',
        'frozen',
        'default_due_time',
        'created_at',
    ]
    list_filter = [
        ('creation_blocked_at', admin.EmptyFieldListFilter),
        ('frozen_at', admin.EmptyFieldListFilter),
    ]
    search_fields = ['user__email']
    list_select_related = ['user']
    inlines = [InboxCodeInline]

    @admin.display(description='Created this month')
    def month_usage(self, obj: TodoAccount) -> int:
        return obj.month_task_count if obj.month_key == month_key(obj.user) else 0

    @admin.display(
        boolean=True, description='Task creation blocked', ordering='creation_blocked_at'
    )
    def blocked(self, obj: TodoAccount) -> bool:
        return obj.is_blocked

    @admin.display(boolean=True, description='Storage frozen', ordering='frozen_at')
    def frozen(self, obj: TodoAccount) -> bool:
        return obj.is_frozen


class LabelAdmin(ReadOnlyModelAdmin):
    list_display = ['name', 'user', 'color', 'description', 'task_count', 'created_at']
    search_fields = ['name', 'description', 'user__email']
    list_select_related = ['user']

    def get_queryset(self, request: HttpRequest):
        return (
            super()
            .get_queryset(request)
            .annotate(_task_count=Count('tasks', filter=Q(tasks__deleted_at__isnull=True)))
        )

    @admin.display(description='Tasks', ordering='_task_count')
    def task_count(self, obj: Label) -> int:
        return obj._task_count  # ty: ignore[unresolved-attribute]


class CommentRevisionInline(admin.TabularInline):
    """The texts a comment had before each edit, oldest first. Read-only."""

    model = CommentRevision
    extra = 0
    fields = ['replaced_at', 'body']
    readonly_fields = ['replaced_at', 'body']
    ordering = ['replaced_at']

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class CommentAdmin(ReadOnlyModelAdmin):
    """Comments, for support. A deleted comment shows its body here too (the user can read it)."""

    list_display = [
        'task_number',
        'user',
        'created_at',
        'edited_at',
        'revision_count',
        'deleted_at',
        'short_body',
    ]
    list_filter = [
        ('edited_at', admin.EmptyFieldListFilter),
        ('deleted_at', admin.EmptyFieldListFilter),
        'created_at',
    ]
    search_fields = ['body', 'task__title', 'task__number', 'user__email']
    list_select_related = ['task', 'user']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    fields = [
        'task',
        'user',
        'created_at',
        'edited_at',
        'revision_count',
        'deleted_at',
        'body',
        'body_text',
    ]
    readonly_fields = fields
    inlines = [CommentRevisionInline]

    @admin.display(description='Task', ordering='task__number')
    def task_number(self, obj: Comment) -> str:
        return f'#{obj.task.number}'

    @admin.display(description='Comment')
    def short_body(self, obj: Comment) -> str:
        return obj.body if len(obj.body) <= 80 else f'{obj.body[:79]}…'


class AttachmentAdmin(ReadOnlyModelAdmin):
    """Read-only: a row is created and removed together with its S3 object and the account's
    `stored_bytes`, which only `apps.todo.services.attachments` keeps in step."""

    list_display = [
        'filename',
        'task',
        'size_bytes',
        'uploaded',
        'inline_type',
        'user',
        'created_at',
    ]
    list_filter = ['uploaded', 'inline_type', 'created_at']
    search_fields = ['filename', 'user__email', 'task__title']
    list_select_related = ['user', 'task']
    date_hierarchy = 'created_at'


class ReminderAdmin(ReadOnlyModelAdmin):
    """Reminders, for support: when each fires and whether it was delivered."""

    list_display = [
        'task_number',
        'user',
        'minutes_before',
        'channel',
        'fire_at',
        'sent_at',
        'skipped',
    ]
    list_filter = [
        'channel',
        'skipped',
        ('sent_at', admin.EmptyFieldListFilter),
        'fire_at',
    ]
    search_fields = ['task__title', 'task__number', 'task__user__email']
    list_select_related = ['task', 'task__user']
    ordering = ['-fire_at']

    @admin.display(description='Task', ordering='task__number')
    def task_number(self, obj: Reminder) -> str:
        return f'#{obj.task.number}'

    @admin.display(description='User', ordering='task__user__email')
    def user(self, obj: Reminder) -> str:
        return str(obj.task.user)


class ReminderDeliveryAdmin(ReadOnlyModelAdmin):
    """The reminder history: every delivery, per channel."""

    list_display = ['task_number', 'task_title', 'user', 'channel', 'note', 'sent_at', 'fire_at']
    list_filter = ['channel', 'note', 'sent_at']
    search_fields = ['task_title', 'task_number', 'user__email']
    list_select_related = ['user']
    date_hierarchy = 'sent_at'
    ordering = ['-sent_at']


class SavedFilterAdmin(ReadOnlyModelAdmin):
    """Saved filters, for support: the name, the owner and the stored params."""

    list_display = ['name', 'user', 'position', 'summary', 'created_at']
    search_fields = ['name', 'user__email']
    list_select_related = ['user']
    ordering = ['user__email', 'position']
    fields = ['user', 'name', 'position', 'params', 'created_at']
    readonly_fields = fields

    @admin.display(description='Search text')
    def summary(self, obj: SavedFilter) -> str:
        return str(obj.params.get('q') or '')


class InboundEmailInline(admin.TabularInline):
    model = InboundEmail
    extra = 0
    can_delete = False
    fields = ['user', 'recipient', 'from_address', 'subject', 'status', 'task', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class InboundMessageAdmin(ReadOnlyModelAdmin):
    """The S3 objects found under `todo/inbound/`: filter by status to find failures, read
    `last_error`, and retry."""

    list_display = [
        's3_key',
        'status',
        'attempts',
        'short_error',
        'next_attempt_at',
        'created_at',
        'processed_at',
    ]
    list_filter = ['status', 'created_at']
    search_fields = ['s3_key', 'last_error']
    inlines = [InboundEmailInline]
    actions = ['retry_action']

    @admin.display(description='Last error')
    def short_error(self, obj: InboundMessage) -> str:
        return obj.last_error[:80]

    def has_retry_permission(self, request: AuthenticatedHttpRequest) -> bool:
        # An action ignores `has_change_permission` (False above), so it needs its own check.
        return request.user.has_perm('todo.change_inboundmessage')

    @admin.action(description='Retry selected failed messages', permissions=['retry'])
    def retry_action(self, request: AuthenticatedHttpRequest, queryset) -> None:
        count = sum(reset_message_for_retry(message) for message in queryset)
        messages.success(request, f'Reset {count} failed message(s) to pending.')


class InboundEmailAdmin(ReadOnlyModelAdmin):
    list_display = ['recipient', 'user', 'from_address', 'subject', 'status', 'note', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['recipient', 'from_address', 'envelope_sender', 'subject', 'user__email']
    list_select_related = ['user']


custom_admin_site.register(TodoSettings, TodoSettingsAdmin)
custom_admin_site.register(Task, TaskAdmin)
custom_admin_site.register(Recurrence, RecurrenceAdmin)
custom_admin_site.register(TodoAccount, TodoAccountAdmin)
custom_admin_site.register(Label, LabelAdmin)
custom_admin_site.register(Comment, CommentAdmin)
custom_admin_site.register(Attachment, AttachmentAdmin)
custom_admin_site.register(Reminder, ReminderAdmin)
custom_admin_site.register(ReminderDelivery, ReminderDeliveryAdmin)
custom_admin_site.register(SavedFilter, SavedFilterAdmin)
custom_admin_site.register(InboundMessage, InboundMessageAdmin)
custom_admin_site.register(InboundEmail, InboundEmailAdmin)
