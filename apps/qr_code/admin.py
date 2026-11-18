"""Admin registrations for the qr_code app.

`QRShareLinkAdmin` is read-only (a link is made through the owner's share panel and the services,
never here) and **never shows a code in full**: the code is the capability to the QR code, so only
its first 6 characters are listed, the search does not look in it and **no lookup may reach it**
(`lookup_allowed`): Django accepts any lookup on a model's own fields from the query string, so
`?code__startswith=Ab` would narrow a full code down one character at a time, however little the
page shows of it. Both admins refuse a lookup with a `code` part, on the model itself or through a
relation (`share_links__code`, `qrcode__share_links__code`).
"""

from django.contrib import admin
from django.db.models.constants import LOOKUP_SEP
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.admin_site import custom_admin_site

from .models import QRCode, QRShareLink, QRShareRecipient
from .services import share


class _NoCodeLookupMixin:
    """Refuse a query-string lookup with a `code` part (a share link's code is a capability)."""

    def lookup_allowed(self, lookup: str, value, request: HttpRequest | None = None) -> bool:
        if 'code' in lookup.split(LOOKUP_SEP):
            return False
        return super().lookup_allowed(lookup, value, request)  # ty: ignore[missing-attribute]


class QRShareLinkInline(admin.TabularInline):
    """The QR code's share links on its page: read-only, codes cut to their first 6 characters."""

    model = QRShareLink
    extra = 0
    can_delete = False
    fields = ['code_hint', 'label', 'state', 'expires_at', 'revoked_at', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @admin.display(description='Code')
    def code_hint(self, obj: QRShareLink) -> str:
        return f'{obj.code[:6]}...'

    @admin.display(description='Now')
    def state(self, obj: QRShareLink) -> str:
        return _state(obj)


class QRShareRecipientInline(admin.TabularInline):
    """The people a link was emailed to: read-only (they are made by the owner's panel)."""

    model = QRShareRecipient
    extra = 0
    can_delete = False
    fields = ['email', 'last_sent_at', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


def _state(link: QRShareLink) -> str:
    state = share.state(link, timezone.now())
    if state == 'active' and not share.is_available(link):
        return 'active (QR code unavailable)'
    return state


class QRCodeAdmin(_NoCodeLookupMixin, admin.ModelAdmin):
    list_display = [
        'id',
        'name',
        'content_preview',
        'qr_format',
        'created_by',
        'scan_count',
        'created_at',
        'deleted_at',
    ]
    list_filter = ['qr_format', 'use_url_shortening', 'created_at', 'deleted_at']
    search_fields = ['content', 'original_url', 'short_code']
    readonly_fields = [
        'id',
        'created_at',
        'updated_at',
        'scan_count',
        'last_scanned_at',
        'deleted_at',
    ]
    inlines = [QRShareLinkInline]

    @admin.display(description='Content')
    def content_preview(self, obj: QRCode) -> str:
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content

    def get_deleted_objects(self, objs, request: AuthenticatedHttpRequest):
        """Don't let the share links' read-only admin block deleting a QR code: the question
        becomes whether the caller holds `delete_qrsharelink` (a superuser does), which is what
        the default `has_delete_permission` asked before the admin was made read-only."""
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        opts = QRShareLink._meta  # ty: ignore[missing-attribute]
        if request.user.has_perm(f'{opts.app_label}.delete_{opts.model_name}'):
            perms_needed.discard(opts.verbose_name)
        return to_delete, model_count, perms_needed, protected


class QRShareLinkAdmin(_NoCodeLookupMixin, admin.ModelAdmin):
    """The share links, for support. Read-only; never shows a code in full."""

    list_display = [
        'code_hint',
        'qrcode_link',
        'owner',
        'state',
        'label',
        'expires_at',
        'created_at',
    ]
    list_filter = ['created_at']
    search_fields = ['qrcode__name', 'qrcode__created_by__email', 'label']
    inlines = [QRShareRecipientInline]
    list_select_related = ['qrcode', 'qrcode__created_by']
    date_hierarchy = 'created_at'
    fields = [
        'code_hint',
        'qrcode_link',
        'owner',
        'state',
        'label',
        'expires_at',
        'revoked_at',
        'created_at',
    ]
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @admin.display(description='Code')
    def code_hint(self, obj: QRShareLink) -> str:
        return f'{obj.code[:6]}...'

    @admin.display(description='QR code', ordering='qrcode__name')
    def qrcode_link(self, obj: QRShareLink) -> str:
        url = reverse('custom_admin:qr_code_qrcode_change', args=[obj.qrcode_id])
        return format_html('<a href="{}">{}</a>', url, obj.qrcode.name)

    @admin.display(description='Owner', ordering='qrcode__created_by__email')
    def owner(self, obj: QRShareLink) -> str:
        return share.owner_of(obj.qrcode).email

    @admin.display(description='Now')
    def state(self, obj: QRShareLink) -> str:
        return _state(obj)


custom_admin_site.register(QRCode, QRCodeAdmin)
custom_admin_site.register(QRShareLink, QRShareLinkAdmin)
