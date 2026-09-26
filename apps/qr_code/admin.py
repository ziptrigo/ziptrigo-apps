"""Admin registrations for the qr_code app."""

from django.contrib import admin

from apps.core.admin_site import custom_admin_site

from .models import QRCode


class QRCodeAdmin(admin.ModelAdmin):
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

    @admin.display(description='Content')
    def content_preview(self, obj: QRCode) -> str:
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content


custom_admin_site.register(QRCode, QRCodeAdmin)
