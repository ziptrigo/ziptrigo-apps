"""Admin registrations for the Notes app: the settings singleton (superusers only) and read-only
`ModelAdmin`s for support -- notes, tags, accounts, share links -- and the abuse tooling (PR 4,
modelled on Short URLs'): a report list with **Dismiss** and **Take down the reported note(s)**, and
**Take down** on the note list. A note is changed through the services (the user's own pages),
never here: editing a row directly would skip the versions, the free slots and the billing.
Deleting a *user* still removes their rows through the cascade
(`apps.accounts.admin.CASCADE_WITH_USER_APP_LABELS` lists `notes`).

**Permissions.** A plain admin action is independent of `has_change_permission` (as Django's own
`delete_selected` is), so without an explicit check any staff user who could merely *view* the lists
could take notes down or dismiss reports. The actions therefore name a permission
(`permissions=['takedown_note']`, matched to `has_takedown_note_permission`): `notes.takedown_note`
(`Note.Meta.permissions`). Superusers pass implicitly. Take down asks again on its own page, collecting
the reason the owner sees and a staff-only note, before it changes anything; every action leaves a
History line (`apps.core.admin_log`).
"""

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import admin_log
from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .forms.admin import TakedownForm
from .models import (
    Note,
    NoteReport,
    NotesAccount,
    NotesSettings,
    NoteVersion,
    ReportStatus,
    ShareLink,
    Tag,
)
from .services import links, reports, take_down_note


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """No add, change or delete: everything is shown, nothing is edited."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class NotesSettingsAdmin(SingletonSettingsAdmin):
    """Singleton settings page: there's only ever the one row `NotesSettings.load()` creates."""


class _NullFilter(admin.SimpleListFilter):
    """A yes/no filter on whether a nullable column is set."""

    column = ''

    def lookups(self, request, model_admin):
        return [('yes', 'Yes'), ('no', 'No')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(**{f'{self.column}__isnull': False})
        if self.value() == 'no':
            return queryset.filter(**{f'{self.column}__isnull': True})
        return queryset


class ArchivedFilter(_NullFilter):
    title = 'archived'
    parameter_name = 'archived'
    column = 'archived_at'


class TrashedFilter(_NullFilter):
    title = 'in the trash'
    parameter_name = 'trashed'
    column = 'deleted_at'


class LockedFilter(_NullFilter):
    title = 'locked'
    parameter_name = 'locked'
    column = 'locked_at'


class TakenDownFilter(_NullFilter):
    title = 'taken down'
    parameter_name = 'taken_down'
    column = 'taken_down_at'


class NoteVersionInline(admin.TabularInline):
    """The versions of a note, newest first. Read-only."""

    model = NoteVersion
    extra = 0
    fields = ['number', 'editor_kind', 'editor', 'created_at', 'updated_at', 'title']
    readonly_fields = fields
    ordering = ['-number']
    show_change_link = False

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class _TakedownPermissionMixin:
    """The permission check behind the takedown actions' `permissions=['takedown_note']` (see the
    module docstring)."""

    def has_takedown_note_permission(self, request: AuthenticatedHttpRequest) -> bool:
        return request.user.has_perm('notes.takedown_note')


def _takedown_confirm_response(
    request: AuthenticatedHttpRequest,
    model_admin: admin.ModelAdmin,
    notes: list[Note],
    *,
    original_queryset,
    action_name: str,
) -> HttpResponse:
    """Shared two-step confirmation of `NoteAdmin` and `NoteReportAdmin`'s take down: `notes` is
    the deduplicated set of notes to take down (what is shown and acted on). The first POST shows
    the page; the second (`apply=yes`, with the reason) does it and logs it."""
    if request.POST.get('apply') == 'yes':
        form = TakedownForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data['reason']
            done = [
                note
                for note in notes
                if take_down_note(
                    note,
                    by=request.user,
                    reason=reason,
                    internal_note=form.cleaned_data['internal_note'],
                )
            ]
            admin_log.log_change(request, done, f'Taken down: {reason}')
            message = f'Took down {len(done)} note(s).'
            skipped = len(notes) - len(done)
            if skipped:
                message += f' {skipped} had already been taken down.'
            messages.success(request, message)
            return redirect(request.path)
    else:
        form = TakedownForm()
    context = {
        **model_admin.admin_site.each_context(request),
        'title': 'Take down note(s)',
        'notes': notes,
        'objects': original_queryset,
        'form': form,
        'opts': model_admin.model._meta,
        'action_checkbox_name': ACTION_CHECKBOX_NAME,
        'action_name': action_name,
    }
    return render(request, 'admin/notes/takedown_confirm.html', context)


class NoteAdmin(_TakedownPermissionMixin, ReadOnlyModelAdmin):
    list_display = [
        'title',
        'user',
        'version',
        'free',
        'paid_through',
        'archived',
        'trashed',
        'locked',
        'taken_down',
        'charged_credits',
        'updated_at',
    ]
    list_filter = [
        'is_free',
        ArchivedFilter,
        TrashedFilter,
        LockedFilter,
        TakenDownFilter,
        'created_at',
    ]
    search_fields = ['title', 'user__email']
    actions = ['take_down_action']
    list_select_related = ['user']
    date_hierarchy = 'created_at'
    inlines = [NoteVersionInline]

    @admin.display(boolean=True, description='Free', ordering='is_free')
    def free(self, obj: Note) -> bool:
        return obj.is_free

    @admin.display(boolean=True, description='Archived', ordering='archived_at')
    def archived(self, obj: Note) -> bool:
        return obj.is_archived

    @admin.display(boolean=True, description='In the Trash', ordering='deleted_at')
    def trashed(self, obj: Note) -> bool:
        return obj.is_trashed

    @admin.display(boolean=True, description='Locked', ordering='locked_at')
    def locked(self, obj: Note) -> bool:
        return obj.is_locked

    @admin.display(boolean=True, description='Taken down', ordering='taken_down_at')
    def taken_down(self, obj: Note) -> bool:
        return obj.is_taken_down

    @admin.action(description='Take down selected note(s)', permissions=['takedown_note'])
    def take_down_action(self, request: AuthenticatedHttpRequest, queryset):
        return _takedown_confirm_response(
            request,
            self,
            list(queryset.select_related('user')),
            original_queryset=queryset,
            action_name='take_down_action',
        )


class TagAdmin(ReadOnlyModelAdmin):
    list_display = ['name', 'user', 'color', 'description', 'note_count', 'created_at']
    search_fields = ['name', 'description', 'user__email']
    list_select_related = ['user']

    def get_queryset(self, request: HttpRequest):
        return (
            super()
            .get_queryset(request)
            .annotate(_note_count=Count('notes', filter=Q(notes__deleted_at__isnull=True)))
        )

    @admin.display(description='Notes', ordering='_note_count')
    def note_count(self, obj: Tag) -> int:
        return obj._note_count  # ty: ignore[missing-attribute]


class NotesAccountAdmin(ReadOnlyModelAdmin):
    list_display = ['user', 'renewal_warned_at', 'created_at']
    search_fields = ['user__email']
    list_select_related = ['user']


class ShareLinkAdmin(ReadOnlyModelAdmin):
    """The share links, for support. **Never shows a code in full**: it is the capability to the
    note, so only its first 6 characters are listed (enough to tell links apart), and the search
    does not look in it."""

    list_display = [
        'code_hint',
        'note_link',
        'owner',
        'mode',
        'state',
        'label',
        'expires_at',
        'last_edited_at',
        'created_at',
    ]
    list_filter = ['mode', 'created_at']
    search_fields = ['note__title', 'note__user__email', 'label']
    list_select_related = ['note', 'note__user']
    date_hierarchy = 'created_at'
    fields = [
        'code_hint',
        'note_link',
        'owner',
        'mode',
        'state',
        'label',
        'expires_at',
        'revoked_at',
        'last_edited_at',
        'created_at',
    ]
    readonly_fields = fields

    @admin.display(description='Code')
    def code_hint(self, obj: ShareLink) -> str:
        return f'{obj.code[:6]}...'

    @admin.display(description='Note', ordering='note__title')
    def note_link(self, obj: ShareLink) -> str:
        url = reverse('custom_admin:notes_note_change', args=[obj.note_id])
        return format_html('<a href="{}">{}</a>', url, obj.note.title)

    @admin.display(description='Owner', ordering='note__user__email')
    def owner(self, obj: ShareLink) -> str:
        return obj.note.user.email

    @admin.display(description='Now')
    def state(self, obj: ShareLink) -> str:
        state = links.state(obj, timezone.now())
        if state == 'active' and not links.is_available(obj):
            return 'active (note unavailable)'
        return state


class NoteReportAdmin(_TakedownPermissionMixin, ReadOnlyModelAdmin):
    """Report list for moderation: filter by status, reason and date, jump to the reported note,
    and dismiss or take down straight from here."""

    list_display = [
        'note_link',
        'reason',
        'status',
        'reporter_email',
        'reporter_ip',
        'created_at',
    ]
    # The report itself opens from its reason; the first column is the reported note.
    list_display_links = ['reason']
    list_filter = ['status', 'reason', 'created_at']
    search_fields = ['note__title', 'note__user__email', 'reporter_email', 'reporter_ip']
    list_select_related = ['note']
    date_hierarchy = 'created_at'
    actions = ['dismiss_action', 'take_down_action']

    @admin.display(description='Note')
    def note_link(self, obj: NoteReport) -> str:
        url = reverse('custom_admin:notes_note_change', args=[obj.note_id])
        return format_html('<a href="{}">{}</a>', url, obj.note.title)

    @admin.action(description='Dismiss selected report(s)', permissions=['takedown_note'])
    def dismiss_action(self, request: AuthenticatedHttpRequest, queryset) -> None:
        count = 0
        for report in queryset.filter(status=ReportStatus.PENDING):
            if reports.dismiss_report(report, by=request.user, note='Dismissed via admin.'):
                admin_log.log_change(request, [report], 'Dismissed.')
                count += 1
        messages.success(request, f'Dismissed {count} report(s).')

    @admin.action(description='Take down the reported note(s)', permissions=['takedown_note'])
    def take_down_action(self, request: AuthenticatedHttpRequest, queryset):
        notes = list(Note.objects.filter(pk__in=queryset.values('note_id')).select_related('user'))
        return _takedown_confirm_response(
            request, self, notes, original_queryset=queryset, action_name='take_down_action'
        )


custom_admin_site.register(NotesSettings, NotesSettingsAdmin)
custom_admin_site.register(Note, NoteAdmin)
custom_admin_site.register(Tag, TagAdmin)
custom_admin_site.register(NotesAccount, NotesAccountAdmin)
custom_admin_site.register(ShareLink, ShareLinkAdmin)
custom_admin_site.register(NoteReport, NoteReportAdmin)
