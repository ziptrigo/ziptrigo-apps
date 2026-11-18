"""Admin registrations for the qr_code app.

`QRShareLinkAdmin` is read-only (a link is made through the owner's share panel and the services,
never here) and **never shows a code in full**: the code is the capability to the QR code, so only
its first 6 characters are listed, the search does not look in it and **no lookup may reach it**
(`lookup_allowed`): Django accepts any lookup on a model's own fields from the query string, so
`?code__startswith=Ab` would narrow a full code down one character at a time, however little the
page shows of it. Every admin here refuses a lookup with a `code` part, on the model itself or
through a relation (`share_links__code`, `qrcode__share_links__code`, `link__code`).

The abuse tooling (issue #5, PR 3; a copy of Notes' `apps.notes.admin`, products may not import each
other): `QRShareReportAdmin` (a read-only report list with **Dismiss** and **Take down the reported
QR code(s)**) and **Take down** on `QRCodeAdmin`.

**Permissions.** A plain admin action is independent of `has_change_permission` (as Django's own
`delete_selected` is), so without an explicit check any staff user who could merely *view* the lists
could take QR codes down or dismiss reports. The actions therefore name a permission
(`permissions=['takedown_qrcode']`, matched to `has_takedown_qrcode_permission`):
`qr_code.takedown_qrcode` (`QRCode.Meta.permissions`). Superusers pass implicitly. Take down asks
again on its own page, collecting the reason the owner sees and a staff-only note, before it changes
anything; every action leaves a History line (`apps.core.admin_log`).
"""

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.db.models.constants import LOOKUP_SEP
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import admin_log
from apps.core.admin_site import custom_admin_site

from .forms.admin import TakedownForm
from .models import QRCode, QRShareLink, QRShareRecipient, QRShareReport, ReportStatus
from .services import share, share_reports, take_down_qrcode


class _NoCodeLookupMixin:
    """Refuse a query-string lookup with a `code` part (a share link's code is a capability)."""

    def lookup_allowed(self, lookup: str, value, request: HttpRequest | None = None) -> bool:
        if 'code' in lookup.split(LOOKUP_SEP):
            return False
        return super().lookup_allowed(lookup, value, request)  # ty: ignore[missing-attribute]


class _ReadOnlyModelAdmin(_NoCodeLookupMixin, admin.ModelAdmin):
    """No add, change or delete: everything is shown, nothing is edited."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class _TakedownPermissionMixin:
    """The permission check behind the takedown actions' `permissions=['takedown_qrcode']` (see the
    module docstring)."""

    def has_takedown_qrcode_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return request.user.has_perm('qr_code.takedown_qrcode')


class TakenDownFilter(admin.SimpleListFilter):
    """A yes/no filter on whether staff took the QR code down."""

    title = 'taken down'
    parameter_name = 'taken_down'

    def lookups(self, request, model_admin):
        return [('yes', 'Yes'), ('no', 'No')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(taken_down_at__isnull=False)
        if self.value() == 'no':
            return queryset.filter(taken_down_at__isnull=True)
        return queryset


def _takedown_confirm_response(
    request: AuthenticatedHttpRequest,
    model_admin: admin.ModelAdmin,
    qrcodes: list[QRCode],
    *,
    original_queryset,
    action_name: str,
) -> HttpResponse:
    """Shared two-step confirmation of `QRCodeAdmin` and `QRShareReportAdmin`'s take down:
    `qrcodes` is the deduplicated set of QR codes to take down (what is shown and acted on). The
    first POST shows the page; the second (`apply=yes`, with the reason) does it and logs it."""
    if request.POST.get('apply') == 'yes':
        form = TakedownForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data['reason']
            done = [
                qrcode
                for qrcode in qrcodes
                if take_down_qrcode(
                    qrcode,
                    by=request.user,
                    reason=reason,
                    internal_note=form.cleaned_data['internal_note'],
                )
            ]
            admin_log.log_change(request, done, f'Taken down: {reason}')
            message = f'Took down {len(done)} QR code(s).'
            skipped = len(qrcodes) - len(done)
            if skipped:
                message += f' {skipped} had already been taken down.'
            messages.success(request, message)
            return redirect(request.path)
    else:
        form = TakedownForm()
    context = {
        **model_admin.admin_site.each_context(request),
        'title': 'Take down QR code(s)',
        'qrcodes': qrcodes,
        'objects': original_queryset,
        'form': form,
        'opts': model_admin.model._meta,
        'action_checkbox_name': ACTION_CHECKBOX_NAME,
        'action_name': action_name,
    }
    return render(request, 'admin/qr_code/takedown_confirm.html', context)


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


class QRCodeAdmin(_TakedownPermissionMixin, _NoCodeLookupMixin, admin.ModelAdmin):
    list_display = [
        'id',
        'name',
        'content_preview',
        'qr_format',
        'created_by',
        'scan_count',
        'created_at',
        'deleted_at',
        'taken_down',
    ]
    list_filter = ['qr_format', 'use_url_shortening', 'created_at', 'deleted_at', TakenDownFilter]
    search_fields = ['content', 'original_url', 'short_code']
    # A takedown is made by the action (`services.takedown`), never by editing these.
    readonly_fields = [
        'id',
        'created_at',
        'updated_at',
        'scan_count',
        'last_scanned_at',
        'deleted_at',
        'taken_down_at',
        'taken_down_by',
        'takedown_reason',
        'takedown_internal_note',
    ]
    inlines = [QRShareLinkInline]
    actions = ['take_down_action']

    @admin.display(boolean=True, description='Taken down', ordering='taken_down_at')
    def taken_down(self, obj: QRCode) -> bool:
        return obj.is_taken_down

    @admin.action(description='Take down selected QR code(s)', permissions=['takedown_qrcode'])
    def take_down_action(self, request: AuthenticatedHttpRequest, queryset):
        return _takedown_confirm_response(
            request,
            self,
            list(queryset.select_related('created_by')),
            original_queryset=queryset,
            action_name='take_down_action',
        )

    @admin.display(description='Content')
    def content_preview(self, obj: QRCode) -> str:
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content

    def get_deleted_objects(self, objs, request: AuthenticatedHttpRequest):
        """Don't let the share links' and reports' read-only admins block deleting a QR code: the
        question becomes whether the caller holds `delete_qrsharelink` / `delete_qrsharereport` (a
        superuser does), which is what the default `has_delete_permission` asked before the admins
        were made read-only."""
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        for model in (QRShareLink, QRShareReport):
            opts = model._meta  # ty: ignore[missing-attribute]
            if request.user.has_perm(f'{opts.app_label}.delete_{opts.model_name}'):
                perms_needed.discard(opts.verbose_name)
        return to_delete, model_count, perms_needed, protected


class QRShareLinkAdmin(_ReadOnlyModelAdmin):
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


class QRShareReportAdmin(_TakedownPermissionMixin, _ReadOnlyModelAdmin):
    """Report list for moderation: filter by status, reason and date, jump to the reported QR code,
    and dismiss or take down straight from here."""

    list_display = [
        'qrcode_link',
        'reason',
        'status',
        'reporter_email',
        'reporter_ip',
        'created_at',
    ]
    # The report itself opens from its reason; the first column is the reported QR code.
    list_display_links = ['reason']
    list_filter = ['status', 'reason', 'created_at']
    search_fields = ['qrcode__name', 'qrcode__created_by__email', 'reporter_email', 'reporter_ip']
    list_select_related = ['qrcode']
    date_hierarchy = 'created_at'
    actions = ['dismiss_action', 'take_down_action']

    @admin.display(description='QR code')
    def qrcode_link(self, obj: QRShareReport) -> str:
        url = reverse('custom_admin:qr_code_qrcode_change', args=[obj.qrcode_id])
        return format_html('<a href="{}">{}</a>', url, obj.qrcode.name)

    @admin.action(description='Dismiss selected report(s)', permissions=['takedown_qrcode'])
    def dismiss_action(self, request: AuthenticatedHttpRequest, queryset) -> None:
        count = 0
        for report in queryset.filter(status=ReportStatus.PENDING):
            if share_reports.dismiss_report(report, by=request.user, note='Dismissed via admin.'):
                admin_log.log_change(request, [report], 'Dismissed.')
                count += 1
        messages.success(request, f'Dismissed {count} report(s).')

    @admin.action(description='Take down the reported QR code(s)', permissions=['takedown_qrcode'])
    def take_down_action(self, request: AuthenticatedHttpRequest, queryset):
        qrcodes = list(
            QRCode.objects.filter(pk__in=queryset.values('qrcode_id')).select_related('created_by')
        )
        return _takedown_confirm_response(
            request, self, qrcodes, original_queryset=queryset, action_name='take_down_action'
        )


custom_admin_site.register(QRCode, QRCodeAdmin)
custom_admin_site.register(QRShareLink, QRShareLinkAdmin)
custom_admin_site.register(QRShareReport, QRShareReportAdmin)
