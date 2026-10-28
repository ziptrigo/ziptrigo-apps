"""Admin registrations for the To-do app: the settings singleton (superusers only) and read-only
`ModelAdmin`s for support -- tasks, accounts, labels. A task is changed through the services (the
user's own pages), never here: editing a row directly would skip numbering, billing and the Trash
rules. Deleting a *user* still removes their rows through the cascade
(`apps.accounts.admin.CASCADE_WITH_USER_APP_LABELS` lists `todo`).
"""

from django.contrib import admin
from django.db.models import Count, Q
from django.http import HttpRequest

from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .models import Label, Task, TodoAccount, TodoSettings
from .services.billing import month_key


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
    list_select_related = ['user']
    date_hierarchy = 'created_at'

    @admin.display(boolean=True, description='Unpaid', ordering='unpaid_at')
    def unpaid(self, obj: Task) -> bool:
        return obj.is_unpaid


class TodoAccountAdmin(ReadOnlyModelAdmin):
    list_display = [
        'user',
        'next_task_number',
        'month_usage',
        'blocked',
        'default_due_time',
        'created_at',
    ]
    list_filter = [('creation_blocked_at', admin.EmptyFieldListFilter)]
    search_fields = ['user__email']
    list_select_related = ['user']

    @admin.display(description='Created this month')
    def month_usage(self, obj: TodoAccount) -> int:
        return obj.month_task_count if obj.month_key == month_key(obj.user) else 0

    @admin.display(
        boolean=True, description='Task creation blocked', ordering='creation_blocked_at'
    )
    def blocked(self, obj: TodoAccount) -> bool:
        return obj.is_blocked


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


custom_admin_site.register(TodoSettings, TodoSettingsAdmin)
custom_admin_site.register(Task, TaskAdmin)
custom_admin_site.register(TodoAccount, TodoAccountAdmin)
custom_admin_site.register(Label, LabelAdmin)
