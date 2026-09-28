"""Admin registrations for file_transfer: a singleton settings page (superusers only, spec
section 9) and read-only `ModelAdmin`s for transfers and download events (for support).
"""

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.admin_site import custom_admin_site

from .models import DownloadEvent, FileTransferSettings, Transfer, TransferFile, TransferRecipient

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


class TransferAdmin(admin.ModelAdmin):
    """Read-only: support can look transfers up, but the services layer
    (`apps.file_transfer.services`) is the only sanctioned way to change one."""

    list_display = [
        'id',
        'owner',
        'status',
        'size_bytes',
        'credits_charged',
        'created_at',
        'expires_at',
    ]
    list_filter = ['status', 'created_at']
    search_fields = ['id', 'slug', 'owner__email', 'sender_email']
    inlines = [TransferFileInline, TransferRecipientInline]
    readonly_fields = _TRANSFER_FIELDS

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


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


custom_admin_site.register(Transfer, TransferAdmin)
custom_admin_site.register(DownloadEvent, DownloadEventAdmin)
custom_admin_site.register(FileTransferSettings, FileTransferSettingsAdmin)
