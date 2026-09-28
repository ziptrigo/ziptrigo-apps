"""Admin registrations for file_transfer: a singleton settings page (superusers only, spec
section 9), read-only `ModelAdmin`s for transfers and download events (for support), and the
abuse-report / block-list tooling (issue #59): a report list with dismiss/take-down actions, and
bulk actions on `Transfer`/`AbuseReport` for takedown and "block this sender", each behind the
same two-step confirmation pattern as `apps.billing.admin`'s credit adjustment tool.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.admin_site import custom_admin_site

from . import services
from .models import (
    AbuseReport,
    AbuseReportStatus,
    BlockedSender,
    DownloadEvent,
    FileTransferSettings,
    Transfer,
    TransferFile,
    TransferRecipient,
)

#: Every field on `Transfer`, shown read-only in the admin (spelled out rather than introspected
#: from `Transfer._meta` -- `ty` has no insight into that metaclass-populated attribute, see the
#: class-level comment in `apps/qr_code/models/qrcode.py`).
_TRANSFER_FIELDS = [
    'id',
    'owner',
    'sender_email',
    'draft_token_hash',
    'sender_ip',
    'anon_cookie_id',
    'email_verification_id',
    'slug',
    'manage_token',
    'message',
    'status',
    'expires_at',
    'max_downloads',
    'password_hash',
    'notify_on_download',
    'size_bytes',
    'completed_at',
    'last_billed_at',
    'accrued',
    'suspended_at',
    'billed_days',
    'credits_charged',
    'expiry_notified_at',
    'zip_status',
    'zip_key',
    'zip_build_started_at',
    'created_at',
    'deleted_at',
    'ended_at',
    'files_deleted_at',
    'held_for_review_at',
    'taken_down_by',
    'taken_down_at',
    'takedown_reason',
]


class TransferFileInline(admin.TabularInline):
    model = TransferFile
    extra = 0
    can_delete = False
    fields = ['name', 'size', 'storage_key', 'checksum', 'uploaded', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class TransferRecipientInline(admin.TabularInline):
    model = TransferRecipient
    extra = 0
    can_delete = False
    fields = ['email', 'last_sent_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class TakedownForm(forms.Form):
    """Intermediate confirmation form for the "take down" bulk action (issue #59): a reason
    (recorded either way) and an opt-in toggle to notify the sender -- default off, so a takedown
    never emails a sender unless staff explicitly choose to."""

    reason: forms.CharField = forms.CharField(
        label='Reason',
        required=True,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Recorded on the transfer, and shown to the sender if you notify them below. '
        'Never reveals who reported it -- keep it about the content, not the reporter.',
    )
    notify_sender: forms.BooleanField = forms.BooleanField(
        label='Notify sender by email ("your files have been deleted")',
        required=False,
        initial=False,
    )


class BlockSenderForm(forms.Form):
    """Intermediate confirmation form for the "block sender" bulk action (issue #59)."""

    block_email: forms.BooleanField = forms.BooleanField(
        label='Block sender email', required=False, initial=True
    )
    block_ip: forms.BooleanField = forms.BooleanField(
        label='Block sender IP', required=False, initial=True
    )
    reason: forms.CharField = forms.CharField(
        label='Reason', required=False, widget=forms.Textarea(attrs={'rows': 2})
    )


def _takedown_confirm_response(
    request: AuthenticatedHttpRequest,
    model_admin: admin.ModelAdmin,
    transfers: list[Transfer],
    *,
    original_queryset,
    action_name: str,
) -> HttpResponse:
    """Shared two-step confirmation body for `TransferAdmin.take_down_action` and
    `AbuseReportAdmin.take_down_action`: `transfers` is the deduplicated set of `Transfer` rows to
    take down (for display, and what's actually acted on); `original_queryset` is the admin's own
    selection (`Transfer` rows for one caller, `AbuseReport` rows for the other) -- needed so the
    confirmation template can re-submit the *original* selection's checkboxes, letting Django's
    own action dispatch (`ModelAdmin.response_action`) reconstruct the right queryset type again
    on the second POST, exactly like the built-in `delete_selected` action does.
    """
    live_transfers = [t for t in transfers if not t.is_ended]
    if request.POST.get('apply') == 'yes':
        form = TakedownForm(request.POST)
        if form.is_valid():
            for transfer in live_transfers:
                services.take_down_transfer(
                    transfer,
                    by=request.user,
                    reason=form.cleaned_data['reason'],
                    notify=form.cleaned_data['notify_sender'],
                )
            messages.success(request, f'Took down {len(live_transfers)} transfer(s).')
            return redirect(request.path)
    else:
        form = TakedownForm()

    context = {
        **model_admin.admin_site.each_context(request),
        'title': 'Take down transfer(s)',
        'transfers': transfers,
        'objects': original_queryset,
        'form': form,
        'opts': model_admin.model._meta,
        'action_checkbox_name': ACTION_CHECKBOX_NAME,
        'action_name': action_name,
    }
    return render(request, 'admin/file_transfer/takedown_confirm.html', context)


def _block_sender_confirm_response(
    request: AuthenticatedHttpRequest,
    model_admin: admin.ModelAdmin,
    transfers: list[Transfer],
    *,
    original_queryset,
    action_name: str,
) -> HttpResponse:
    """Shared two-step confirmation body for the "block sender of this transfer" convenience
    action, on both `TransferAdmin` and `AbuseReportAdmin` -- same shape as
    `_takedown_confirm_response`."""
    if request.POST.get('apply') == 'yes':
        form = BlockSenderForm(request.POST)
        if form.is_valid():
            created = []
            for transfer in transfers:
                created += services.blocklist.block_transfer_sender(
                    transfer,
                    created_by=request.user,
                    reason=form.cleaned_data['reason'],
                    block_email=form.cleaned_data['block_email'],
                    block_ip=form.cleaned_data['block_ip'],
                )
            messages.success(request, f'Added {len(created)} block-list entry/entries.')
            return redirect(request.path)
    else:
        form = BlockSenderForm()

    context = {
        **model_admin.admin_site.each_context(request),
        'title': 'Block sender(s)',
        'transfers': transfers,
        'objects': original_queryset,
        'form': form,
        'opts': model_admin.model._meta,
        'action_checkbox_name': ACTION_CHECKBOX_NAME,
        'action_name': action_name,
    }
    return render(request, 'admin/file_transfer/block_sender_confirm.html', context)


class TransferAdmin(admin.ModelAdmin):
    """Read-only in the ordinary sense: support can look transfers up, but the services layer
    (`apps.file_transfer.services`) is the only sanctioned way to change one directly. The bulk
    actions below (issue #59) are the one exception -- same as Django's own built-in
    `delete_selected`, an admin *action* is independent of `has_change_permission`, so these work
    even though the change form itself stays read-only.
    """

    list_display = [
        'id',
        'owner',
        'status',
        'held_for_review_at',
        'size_bytes',
        'credits_charged',
        'created_at',
        'expires_at',
    ]
    list_filter = ['status', 'created_at']
    search_fields = ['id', 'slug', 'owner__email', 'sender_email']
    inlines = [TransferFileInline, TransferRecipientInline]
    readonly_fields = _TRANSFER_FIELDS
    actions = ['take_down_action', 'release_hold_action', 'block_sender_action']

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @admin.action(description='Take down selected transfer(s)')
    def take_down_action(self, request: AuthenticatedHttpRequest, queryset):
        return _takedown_confirm_response(
            request,
            self,
            list(queryset),
            original_queryset=queryset,
            action_name='take_down_action',
        )

    @admin.action(description='Release abuse-review hold (dismisses its pending reports)')
    def release_hold_action(self, request: AuthenticatedHttpRequest, queryset) -> None:
        now = timezone.now()
        count = 0
        for transfer in queryset.filter(held_for_review_at__isnull=False):
            services.release_hold(transfer)
            transfer.reports.filter(status=AbuseReportStatus.PENDING).update(
                status=AbuseReportStatus.DISMISSED,
                reviewed_by=request.user,
                reviewed_at=now,
                resolution_note='Hold released via admin.',
            )
            count += 1
        messages.success(request, f'Released the hold on {count} transfer(s).')

    @admin.action(description='Block sender of selected transfer(s)')
    def block_sender_action(self, request: AuthenticatedHttpRequest, queryset):
        return _block_sender_confirm_response(
            request,
            self,
            list(queryset),
            original_queryset=queryset,
            action_name='block_sender_action',
        )


class DownloadEventAdmin(admin.ModelAdmin):
    list_display = ['id', 'transfer', 'file', 'ip', 'created_at']
    list_filter = ['created_at']
    search_fields = ['transfer__id', 'transfer__slug']
    readonly_fields = ['transfer', 'file', 'ip', 'created_at']

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class FileTransferSettingsAdmin(admin.ModelAdmin):
    """Singleton settings page (spec section 9): superusers only, and there's only ever the one
    row `FileTransferSettings.load()` creates."""

    def _is_superuser(self, request: AuthenticatedHttpRequest) -> bool:
        return bool(request.user.is_active and request.user.is_superuser)

    def has_module_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return self._is_superuser(request)

    def has_view_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return self._is_superuser(request)

    def has_add_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return self._is_superuser(request) and not FileTransferSettings.objects.exists()

    def has_change_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return self._is_superuser(request)

    def has_delete_permission(self, request: AuthenticatedHttpRequest, obj=None) -> bool:
        return False

    def changelist_view(
        self, request: AuthenticatedHttpRequest, extra_context=None
    ) -> HttpResponse:
        """Skip the changelist entirely -- there's only ever one row."""
        settings_row = FileTransferSettings.load()
        return redirect('custom_admin:file_transfer_filetransfersettings_change', settings_row.pk)


#: Every field on `AbuseReport`, shown read-only -- reports come from the public form (or, rarely,
#: an existing row edited through `services.reports`), never created/edited by hand in the admin;
#: the `dismiss_action`/`take_down_action` bulk actions below are the sanctioned way to resolve one.
_REPORT_FIELDS = [
    'id',
    'transfer',
    'reason',
    'details',
    'reporter_email',
    'reporter_ip',
    'status',
    'reviewed_by',
    'reviewed_at',
    'resolution_note',
    'created_at',
]


class AbuseReportAdmin(admin.ModelAdmin):
    """Report list for support/moderation (issue #59): filter by status/reason/date, jump to the
    reported transfer, and dismiss or take down straight from here."""

    list_display = [
        'id',
        'transfer_link',
        'reason',
        'status',
        'reporter_email',
        'reporter_ip',
        'created_at',
    ]
    list_filter = ['status', 'reason', 'created_at']
    search_fields = ['transfer__id', 'transfer__slug', 'reporter_email', 'reporter_ip']
    readonly_fields = _REPORT_FIELDS
    date_hierarchy = 'created_at'
    actions = ['dismiss_action', 'take_down_action', 'block_sender_action']

    @admin.display(description='Transfer')
    def transfer_link(self, obj: AbuseReport) -> str:
        url = reverse('custom_admin:file_transfer_transfer_change', args=[obj.transfer_id])
        return format_html('<a href="{}">{}</a>', url, obj.transfer_id)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @admin.action(description='Dismiss selected report(s)')
    def dismiss_action(self, request: AuthenticatedHttpRequest, queryset) -> None:
        count = 0
        for report in queryset.filter(status=AbuseReportStatus.PENDING):
            services.dismiss_report(report, by=request.user, note='Dismissed via admin.')
            count += 1
        messages.success(request, f'Dismissed {count} report(s).')

    @admin.action(description='Take down the reported transfer(s)')
    def take_down_action(self, request: AuthenticatedHttpRequest, queryset):
        transfer_ids = queryset.values_list('transfer_id', flat=True).distinct()
        transfers = list(Transfer.objects.filter(pk__in=transfer_ids))
        return _takedown_confirm_response(
            request,
            self,
            transfers,
            original_queryset=queryset,
            action_name='take_down_action',
        )

    @admin.action(description="Block the reported transfer(s)'s sender(s)")
    def block_sender_action(self, request: AuthenticatedHttpRequest, queryset):
        transfer_ids = queryset.values_list('transfer_id', flat=True).distinct()
        transfers = list(Transfer.objects.filter(pk__in=transfer_ids))
        return _block_sender_confirm_response(
            request,
            self,
            transfers,
            original_queryset=queryset,
            action_name='block_sender_action',
        )


class BlockedSenderAdmin(admin.ModelAdmin):
    """The block list itself (issue #59): plain CRUD for staff, plus the convenience "block
    sender" actions above that create entries here directly from a transfer or its reports."""

    list_display = ['id', 'kind', 'value', 'reason', 'created_by', 'created_at', 'expires_at']
    list_filter = ['kind', 'created_at']
    search_fields = ['value', 'reason']
    readonly_fields = ['id', 'created_by', 'created_at']

    def save_model(
        self, request: AuthenticatedHttpRequest, obj: BlockedSender, form, change: bool
    ) -> None:
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


custom_admin_site.register(Transfer, TransferAdmin)
custom_admin_site.register(DownloadEvent, DownloadEventAdmin)
custom_admin_site.register(FileTransferSettings, FileTransferSettingsAdmin)
custom_admin_site.register(AbuseReport, AbuseReportAdmin)
custom_admin_site.register(BlockedSender, BlockedSenderAdmin)
