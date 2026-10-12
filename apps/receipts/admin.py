"""Admin registrations for Receipts: the settings singleton (superusers only), and read-only
`ModelAdmin`s for support -- receipts, inbound messages and emails, accounts. The services layer
(`apps.receipts.services`) is the only sanctioned way to change a receipt: deleting one here would
orphan its S3 objects and leave the account's stored-bytes count wrong.
"""

from django.contrib import admin, messages
from django.db.models import Count
from django.http import HttpRequest

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .models import (
    InboundEmail,
    InboundMessage,
    InboxCode,
    Receipt,
    ReceiptsAccount,
    ReceiptsSettings,
    Vendor,
    VendorAlias,
)
from .services.inbound import reset_message_for_retry


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """No add, change or delete: everything is shown, nothing is edited."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class ReceiptsSettingsAdmin(SingletonSettingsAdmin):
    """Singleton settings page: there's only ever the one row `ReceiptsSettings.load()` creates.
    The storage price is on the Billing settings page, shared with file transfer."""


class ReceiptAdmin(ReadOnlyModelAdmin):
    list_display = [
        'id',
        'user',
        'vendor',
        'vendor_extracted',
        'amount',
        'currency',
        'receipt_date',
        'status',
        'failure_reason',
        'source',
        'size_bytes',
        'created_at',
        'deleted_at',
    ]
    list_filter = ['status', 'failure_reason', 'source', 'created_at']
    search_fields = [
        'id',
        'user__email',
        'vendor',
        'vendor_extracted',
        'original_filename',
        'sha256',
    ]
    list_select_related = ['user']
    date_hierarchy = 'created_at'


class VendorAliasInline(admin.TabularInline):
    model = VendorAlias
    extra = 0
    can_delete = False
    fields = ['name', 'key', 'source', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class VendorAdmin(ReadOnlyModelAdmin):
    """A user's vendors with the names they are known by, to see why a receipt landed where it did.
    Read-only: renaming and merging go through the services, which keep every receipt's vendor text
    and the aliases in step."""

    list_display = ['name', 'user', 'vetted', 'receipt_count', 'created_at']
    list_filter = [('vetted_at', admin.EmptyFieldListFilter), 'created_at']
    search_fields = ['name', 'user__email', 'aliases__name']
    list_select_related = ['user']
    inlines = [VendorAliasInline]

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).annotate(_receipt_count=Count('receipts'))

    @admin.display(boolean=True, description='Vetted', ordering='vetted_at')
    def vetted(self, obj: Vendor) -> bool:
        return obj.vetted

    @admin.display(description='Receipts', ordering='_receipt_count')
    def receipt_count(self, obj: Vendor) -> int:
        return obj._receipt_count  # ty: ignore[unresolved-attribute]


class InboundEmailInline(admin.TabularInline):
    model = InboundEmail
    extra = 0
    can_delete = False
    fields = ['user', 'recipient', 'from_address', 'subject', 'status', 'created_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class InboundMessageAdmin(ReadOnlyModelAdmin):
    """The S3 objects found under `receipts/inbound/`: filter by status to find failures, read
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
        return request.user.has_perm('receipts.change_inboundmessage')

    @admin.action(description='Retry selected failed messages', permissions=['retry'])
    def retry_action(self, request: AuthenticatedHttpRequest, queryset) -> None:
        count = sum(reset_message_for_retry(message) for message in queryset)
        messages.success(request, f'Reset {count} failed message(s) to pending.')


class InboundEmailAdmin(ReadOnlyModelAdmin):
    list_display = ['recipient', 'user', 'from_address', 'subject', 'status', 'note', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['recipient', 'from_address', 'envelope_sender', 'subject', 'user__email']
    list_select_related = ['user']


class InboxCodeInline(admin.TabularInline):
    model = InboxCode
    extra = 0
    can_delete = False
    fields = ['code', 'active', 'created_at', 'retired_at']
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class ReceiptsAccountAdmin(ReadOnlyModelAdmin):
    list_display = ['user', 'stored_bytes', 'frozen_at', 'last_billed_at', 'billed_days']
    list_filter = [('frozen_at', admin.EmptyFieldListFilter)]
    search_fields = ['user__email']
    list_select_related = ['user']
    inlines = [InboxCodeInline]


custom_admin_site.register(ReceiptsSettings, ReceiptsSettingsAdmin)
custom_admin_site.register(Receipt, ReceiptAdmin)
custom_admin_site.register(Vendor, VendorAdmin)
custom_admin_site.register(InboundMessage, InboundMessageAdmin)
custom_admin_site.register(InboundEmail, InboundEmailAdmin)
custom_admin_site.register(ReceiptsAccount, ReceiptsAccountAdmin)
