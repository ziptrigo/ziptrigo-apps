"""Admin registration for the feedback app: triage by status, with bulk status changes."""

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core.admin_site import custom_admin_site

from .models import Feedback, FeedbackStatus

PREVIEW_LENGTH = 80


class FeedbackAdmin(admin.ModelAdmin):
    """Feedback comes from the site only, so everything is read-only except `status`."""

    list_display = ['created_at', 'submitter', 'status', 'preview']
    list_filter = ['status', 'created_at']
    search_fields = ['description', 'created_by__email']
    date_hierarchy = 'created_at'
    list_select_related = ['created_by']
    fields = ['id', 'created_at', 'updated_at', 'created_by', 'description_text', 'status']
    readonly_fields = ['id', 'created_at', 'updated_at', 'created_by', 'description_text']
    actions = ['mark_new', 'mark_in_process', 'mark_closed']

    @admin.display(description='Submitted by', ordering='created_by__email')
    def submitter(self, obj: Feedback) -> str:
        return obj.created_by.email if obj.created_by else '(deleted user)'

    @admin.display(description='Feedback')
    def preview(self, obj: Feedback) -> str:
        text = ' '.join(obj.description.split())
        return text if len(text) <= PREVIEW_LENGTH else f'{text[:PREVIEW_LENGTH].rstrip()}…'

    @admin.display(description='Feedback')
    def description_text(self, obj: Feedback) -> SafeString:
        return format_html('<div style="white-space: pre-wrap">{}</div>', obj.description)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def _mark(self, request: HttpRequest, queryset: QuerySet, status: FeedbackStatus) -> None:
        # `update()` skips `auto_now`, so `updated_at` is set explicitly.
        count = queryset.update(status=status, updated_at=timezone.now())
        self.message_user(
            request, f'Marked {count} feedback as {status.label}.', level=messages.SUCCESS
        )

    @admin.action(description='Mark selected as New', permissions=['change'])
    def mark_new(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._mark(request, queryset, FeedbackStatus.NEW)

    @admin.action(description='Mark selected as In process', permissions=['change'])
    def mark_in_process(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._mark(request, queryset, FeedbackStatus.IN_PROCESS)

    @admin.action(description='Mark selected as Closed', permissions=['change'])
    def mark_closed(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._mark(request, queryset, FeedbackStatus.CLOSED)


custom_admin_site.register(Feedback, FeedbackAdmin)
