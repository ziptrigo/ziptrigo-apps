"""Admin registrations for the Notes app: the settings singleton (superusers only) and read-only
`ModelAdmin`s for support -- notes, tags, accounts. A note is changed through the services (the
user's own pages), never here: editing a row directly would skip the versions, the free slots and
the billing. Deleting a *user* still removes their rows through the cascade
(`apps.accounts.admin.CASCADE_WITH_USER_APP_LABELS` lists `notes`).
"""

from django.contrib import admin
from django.db.models import Count, Q
from django.http import HttpRequest

from apps.core.admin_singleton import SingletonSettingsAdmin
from apps.core.admin_site import custom_admin_site

from .models import Note, NotesAccount, NotesSettings, NoteVersion, Tag


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


class NoteAdmin(ReadOnlyModelAdmin):
    list_display = [
        'title',
        'user',
        'version',
        'free',
        'paid_through',
        'archived',
        'trashed',
        'locked',
        'charged_credits',
        'updated_at',
    ]
    list_filter = ['is_free', ArchivedFilter, TrashedFilter, LockedFilter, 'created_at']
    search_fields = ['title', 'user__email']
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


custom_admin_site.register(NotesSettings, NotesSettingsAdmin)
custom_admin_site.register(Note, NoteAdmin)
custom_admin_site.register(Tag, TagAdmin)
custom_admin_site.register(NotesAccount, NotesAccountAdmin)
