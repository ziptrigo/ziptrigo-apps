"""Admin registrations for Short URLs: the settings singleton (superusers only), a read-only
list of links for support, and the abuse tooling (issue #162, modelled on file transfer's, issue
#59): a report list with dismiss / take down actions, bulk actions on links and reports for
takedown, "block the destination domain", "deactivate the owner's account" and "release hold",
and the destination block list itself. A link is changed through the services (the owner's
pages), never here: editing a row directly would skip the code, billing and status rules.

**Permissions.** A plain admin action is independent of `has_change_permission` (as Django's own
`delete_selected` is), so without an explicit check any staff user who could merely *view* the
list could take links down, release holds or add block-list entries. The destructive actions
therefore name a permission (`permissions=[...]`, matched to a `has_<name>_permission` method on
`_AbuseActionPermissionMixin`): `short_url.takedown_link` (`ShortLink.Meta.permissions`) for take
down, release hold and dismiss; `short_url.add_blockeddomain` for blocking a domain; and
`takedown_link` *and* `accounts.change_user` for deactivating an account. Superusers pass
implicitly. The take down and block actions ask again on their own page before they change
anything.
"""

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html

from apps.accounts.http import AuthenticatedHttpRequest
from apps.accounts.models import User
from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .forms.admin import BlockDomainForm, DeactivateOwnerForm, TakedownForm
from .models import (
    BlockedDomain,
    LinkReport,
    LinkStatus,
    ReportStatus,
    ShortLink,
    ShortUrlSettings,
)
from .services import blocklist, display_state, reports, short_url, take_down_link


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """No add, change or delete: everything is shown, nothing is edited."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class ShortUrlSettingsAdmin(SingletonSettingsAdmin):
    """Singleton settings page: there's only ever the one row `ShortUrlSettings.load()` creates."""

    fieldsets = [
        (
            'Prices (credits, one-off, per link)',
            {
                'fields': [
                    'custom_alias_price',
                    'password_price',
                    'analytics_price',
                    'extra_link_price',
                ],
                'description': 'A price of 0 makes that extra free.',
            },
        ),
        ('Allowances (per user)', {'fields': ['free_active_links', 'max_active_links']}),
        (
            'Analytics and abuse',
            {'fields': ['click_event_retention_days', 'auto_hold_report_threshold']},
        ),
    ]


def _skipped_note(skipped: int) -> str:
    """Tail for a takedown message when some links were not taken down: they were deleted by
    their owner (or taken down by someone else) after the list was read, and `take_down_link`
    leaves such a link alone."""
    if skipped == 1:
        return ' 1 was skipped because it had already ended.'
    return f' {skipped} were skipped because they had already ended.'


def _confirm_context(
    request: AuthenticatedHttpRequest,
    model_admin: admin.ModelAdmin,
    *,
    title: str,
    links: list[ShortLink],
    original_queryset,
    action_name: str,
    form,
) -> dict:
    """What the confirmation templates need. `original_queryset` is the admin's own selection
    (links for one caller, reports for the other): its checkboxes are re-submitted so Django's
    action dispatch rebuilds the right queryset on the second POST, like `delete_selected`."""
    return {
        **model_admin.admin_site.each_context(request),
        'title': title,
        'links': links,
        'objects': original_queryset,
        'form': form,
        'opts': model_admin.model._meta,
        'action_checkbox_name': ACTION_CHECKBOX_NAME,
        'action_name': action_name,
    }


def _takedown_confirm_response(
    request: AuthenticatedHttpRequest,
    model_admin: admin.ModelAdmin,
    links: list[ShortLink],
    *,
    original_queryset,
    action_name: str,
) -> HttpResponse:
    """Shared two-step confirmation of `ShortLinkAdmin` and `LinkReportAdmin`'s take down: `links`
    is the deduplicated set of links to take down (what is shown and acted on)."""
    live = [link for link in links if link.status in (LinkStatus.ACTIVE, LinkStatus.DISABLED)]
    if request.POST.get('apply') == 'yes':
        form = TakedownForm(request.POST)
        if form.is_valid():
            taken_down = sum(
                take_down_link(
                    link,
                    by=request.user,
                    reason=form.cleaned_data['reason'],
                    internal_note=form.cleaned_data['internal_note'],
                    notify=form.cleaned_data['notify_owner'],
                )
                for link in live
            )
            message = f'Took down {taken_down} link(s).'
            if taken_down < len(live):
                message += _skipped_note(len(live) - taken_down)
            messages.success(request, message)
            return redirect(request.path)
    else:
        form = TakedownForm()
    context = _confirm_context(
        request,
        model_admin,
        title='Take down link(s)',
        links=links,
        original_queryset=original_queryset,
        action_name=action_name,
        form=form,
    )
    return render(request, 'admin/short_url/takedown_confirm.html', context)


def _block_domain_confirm_response(
    request: AuthenticatedHttpRequest,
    model_admin: admin.ModelAdmin,
    links: list[ShortLink],
    *,
    original_queryset,
    action_name: str,
) -> HttpResponse:
    """Shared two-step confirmation of "block the destination domain"."""
    if request.POST.get('apply') == 'yes':
        form = BlockDomainForm(request.POST)
        if form.is_valid():
            added = 0
            for link in links:
                entry = blocklist.block_link_destination(
                    link,
                    created_by=request.user,
                    reason=form.cleaned_data['reason'],
                    include_subdomains=form.cleaned_data['include_subdomains'],
                )
                added += entry is not None
            messages.success(
                request,
                f'Added {added} block-list entry/entries. Links pointing there stop working at '
                'once.',
            )
            return redirect(request.path)
    else:
        form = BlockDomainForm()
    context = _confirm_context(
        request,
        model_admin,
        title='Block destination domain(s)',
        links=links,
        original_queryset=original_queryset,
        action_name=action_name,
        form=form,
    )
    return render(request, 'admin/short_url/block_domain_confirm.html', context)


def _deactivate_owner_confirm_response(
    request: AuthenticatedHttpRequest,
    model_admin: admin.ModelAdmin,
    links: list[ShortLink],
    *,
    original_queryset,
    action_name: str,
) -> HttpResponse:
    """Shared two-step confirmation of "deactivate the owner's account": sets `User.status` to
    inactive, which session login and the JWT API already refuse on their own, like file
    transfer's block-sender form. Optionally takes the owner's other live links down too, since a
    deactivated account's links keep redirecting."""
    owners = list({link.owner_id: link.owner for link in links}.values())
    if request.POST.get('apply') == 'yes':
        form = DeactivateOwnerForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data['reason']
            for owner in owners:
                if owner.status != User.STATUS_INACTIVE:
                    owner.deactivate(reason=reason)
            message = f'Deactivated {len(owners)} account(s).'
            if form.cleaned_data['take_down_links']:
                targets = ShortLink.objects.filter(
                    owner__in=owners, status__in=(LinkStatus.ACTIVE, LinkStatus.DISABLED)
                )
                done = sum(
                    take_down_link(
                        target,
                        by=request.user,
                        reason=reason,
                        internal_note='Owner deactivated.',
                        notify=False,
                    )
                    for target in targets
                )
                message += f' Took down {done} link(s).'
            messages.success(request, message)
            return redirect(request.path)
    else:
        form = DeactivateOwnerForm()
    context = _confirm_context(
        request,
        model_admin,
        title="Deactivate owner's account(s)",
        links=links,
        original_queryset=original_queryset,
        action_name=action_name,
        form=form,
    )
    context['owners'] = owners
    return render(request, 'admin/short_url/deactivate_owner_confirm.html', context)


class _AbuseActionPermissionMixin:
    """The permission checks behind the abuse actions' `permissions=[...]` (see the module
    docstring)."""

    def has_takedown_link_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return request.user.has_perm('short_url.takedown_link')

    def has_block_domain_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return request.user.has_perm('short_url.add_blockeddomain')

    def has_deactivate_owner_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return request.user.has_perm('short_url.takedown_link') and request.user.has_perm(
            'accounts.change_user'
        )


class ShortLinkAdmin(_AbuseActionPermissionMixin, ReadOnlyModelAdmin):
    """Read-only in the ordinary sense (the change form has no save button), plus the abuse
    actions: they are admin *actions*, independent of `has_change_permission`, so they work
    without loosening the model's read-only-ness."""

    list_display = [
        'code',
        'owner',
        'destination_preview',
        'status',
        'state',
        'held_for_review_at',
        'pending_reports',
        'click_count',
        'is_alias',
        'created_at',
    ]
    list_filter = [
        'status',
        ('held_for_review_at', admin.EmptyFieldListFilter),
        'is_alias',
        'created_at',
    ]
    search_fields = ['code_key', 'destination', 'title', 'owner__email']
    list_select_related = ['owner']
    date_hierarchy = 'created_at'
    readonly_fields = ['public_link']
    actions = [
        'take_down_action',
        'block_domain_action',
        'deactivate_owner_action',
        'release_hold_action',
    ]

    def get_queryset(self, request: HttpRequest):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _pending_reports=Count('reports', filter=Q(reports__status=ReportStatus.PENDING))
            )
        )

    @admin.display(description='Destination')
    def destination_preview(self, obj: ShortLink) -> str:
        return obj.destination if len(obj.destination) <= 60 else obj.destination[:57] + '...'

    @admin.display(description='Now')
    def state(self, obj: ShortLink) -> str:
        return display_state(obj)

    @admin.display(description='Pending reports', ordering='_pending_reports')
    def pending_reports(self, obj: ShortLink) -> int:
        return getattr(obj, '_pending_reports', 0)

    @admin.display(description='Short link')
    def public_link(self, obj: ShortLink) -> str:
        return short_url(obj.code)

    @admin.action(description='Take down selected link(s)', permissions=['takedown_link'])
    def take_down_action(self, request: AuthenticatedHttpRequest, queryset):
        return _takedown_confirm_response(
            request,
            self,
            list(queryset),
            original_queryset=queryset,
            action_name='take_down_action',
        )

    @admin.action(
        description="Block the selected link(s)' destination domain", permissions=['block_domain']
    )
    def block_domain_action(self, request: AuthenticatedHttpRequest, queryset):
        return _block_domain_confirm_response(
            request,
            self,
            list(queryset),
            original_queryset=queryset,
            action_name='block_domain_action',
        )

    @admin.action(
        description="Deactivate the selected link(s)' owner's account",
        permissions=['deactivate_owner'],
    )
    def deactivate_owner_action(self, request: AuthenticatedHttpRequest, queryset):
        return _deactivate_owner_confirm_response(
            request,
            self,
            list(queryset.select_related('owner')),
            original_queryset=queryset,
            action_name='deactivate_owner_action',
        )

    @admin.action(
        description='Release abuse-review hold (dismisses its pending reports)',
        permissions=['takedown_link'],
    )
    def release_hold_action(self, request: AuthenticatedHttpRequest, queryset) -> None:
        count = 0
        for link in queryset.filter(held_for_review_at__isnull=False):
            reports.release_hold_and_dismiss(link, by=request.user, note='Hold released via admin.')
            count += 1
        messages.success(request, f'Released the hold on {count} link(s).')


class LinkReportAdmin(_AbuseActionPermissionMixin, ReadOnlyModelAdmin):
    """Report list for moderation: filter by status, reason and date, jump to the reported link,
    and dismiss or take down straight from here."""

    list_display = [
        'id',
        'link_link',
        'reason',
        'status',
        'reporter_email',
        'reporter_ip',
        'created_at',
    ]
    list_filter = ['status', 'reason', 'created_at']
    search_fields = ['link__code_key', 'link__destination', 'reporter_email', 'reporter_ip']
    list_select_related = ['link']
    date_hierarchy = 'created_at'
    actions = [
        'dismiss_action',
        'take_down_action',
        'block_domain_action',
        'deactivate_owner_action',
    ]

    @admin.display(description='Link')
    def link_link(self, obj: LinkReport) -> str:
        url = reverse('custom_admin:short_url_shortlink_change', args=[obj.link_id])
        return format_html('<a href="{}">{}</a>', url, obj.link.code)

    @admin.action(description='Dismiss selected report(s)', permissions=['takedown_link'])
    def dismiss_action(self, request: AuthenticatedHttpRequest, queryset) -> None:
        count = 0
        for report in queryset.filter(status=ReportStatus.PENDING):
            count += reports.dismiss_report(report, by=request.user, note='Dismissed via admin.')
        messages.success(request, f'Dismissed {count} report(s).')

    @admin.action(description='Take down the reported link(s)', permissions=['takedown_link'])
    def take_down_action(self, request: AuthenticatedHttpRequest, queryset):
        links = list(ShortLink.objects.filter(pk__in=queryset.values('link_id')))
        return _takedown_confirm_response(
            request, self, links, original_queryset=queryset, action_name='take_down_action'
        )

    @admin.action(
        description="Block the reported link(s)' destination domain", permissions=['block_domain']
    )
    def block_domain_action(self, request: AuthenticatedHttpRequest, queryset):
        links = list(ShortLink.objects.filter(pk__in=queryset.values('link_id')))
        return _block_domain_confirm_response(
            request, self, links, original_queryset=queryset, action_name='block_domain_action'
        )

    @admin.action(
        description="Deactivate the reported link(s)' owner's account",
        permissions=['deactivate_owner'],
    )
    def deactivate_owner_action(self, request: AuthenticatedHttpRequest, queryset):
        links = list(
            ShortLink.objects.filter(pk__in=queryset.values('link_id')).select_related('owner')
        )
        return _deactivate_owner_confirm_response(
            request,
            self,
            links,
            original_queryset=queryset,
            action_name='deactivate_owner_action',
        )


class BlockedDomainAdmin(admin.ModelAdmin):
    """The destination block list: plain CRUD for staff (with `expires_at` for a temporary block),
    plus the convenience "block the destination domain" actions on links and reports."""

    list_display = ['value', 'reason', 'created_by', 'created_at', 'expires_at', 'in_force']
    list_filter = ['created_at']
    search_fields = ['value', 'reason']
    readonly_fields = ['id', 'created_by', 'created_at']

    @admin.display(description='In force', boolean=True)
    def in_force(self, obj: BlockedDomain) -> bool:
        return not obj.is_expired

    def save_model(
        self, request: AuthenticatedHttpRequest, obj: BlockedDomain, form, change: bool
    ) -> None:
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


custom_admin_site.register(ShortUrlSettings, ShortUrlSettingsAdmin)
custom_admin_site.register(ShortLink, ShortLinkAdmin)
custom_admin_site.register(LinkReport, LinkReportAdmin)
custom_admin_site.register(BlockedDomain, BlockedDomainAdmin)
