import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.core.markdown import normalize_for_search, search_text

from ..limits import TITLE_MAX
from .tag import Tag


class EditorKind(models.TextChoices):
    """Who saved a version: the note's owner, another signed-in user (through a share link) or
    someone who isn't signed in (through an editable share link)."""

    OWNER = 'owner', 'Owner'
    USER = 'user', 'Signed-in user'
    ANONYMOUS = 'anonymous', 'Someone'


class Note(models.Model):
    """One Markdown note.

    `version` counts the saves of the title or content (a save with a stale `base_version` is a
    conflict, see `services.notes.save_note`); `updated_at` is set by the services on exactly those
    saves, not `auto_now`, so archiving, tagging and ordering don't move it. `title_text` and
    `content_text` are the normalised copies search runs over (`save()` keeps them in step).

    Billing: a note created in a free slot is `is_free` for good; any other has `paid_through`, the
    date it must be renewed by. `locked_at` is set while a renewal could not be paid.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `NoteVersion.note`.
    versions: ClassVar['models.Manager']
    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    user_id: uuid.UUID
    # Set by `views.lists` on the notes a list shows (not stored).
    excerpt: str

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notes'),
    )
    title = cast(str, models.CharField(max_length=TITLE_MAX))
    content = cast(str, models.TextField(blank=True))
    #: `apps.core.markdown.normalize_for_search(title)`.
    title_text = cast(str, models.CharField(max_length=TITLE_MAX, blank=True, editable=False))
    #: `apps.core.markdown.search_text(content, profile='extended')`.
    content_text = cast(str, models.TextField(blank=True, editable=False))

    tags = cast('models.Manager', models.ManyToManyField(Tag, blank=True, related_name='notes'))
    #: Manual order (smaller first); a new note takes the lowest value minus one.
    position = cast(int, models.IntegerField(default=0))
    version = cast(int, models.PositiveIntegerField(default=1))

    archived_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    deleted_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    is_free = cast(bool, models.BooleanField(default=False))
    paid_through = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    locked_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    charged_credits = cast(
        int, models.PositiveIntegerField(default=0, help_text='What creating this note cost.')
    )

    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    updated_at = cast(datetime, models.DateTimeField(default=timezone.now))

    class Meta:
        indexes = [
            models.Index(fields=['user', 'deleted_at', 'archived_at', 'position']),
            models.Index(fields=['user', 'updated_at']),
            models.Index(
                fields=['paid_through'],
                name='notes_note_renewal_due',
                condition=Q(is_free=False, deleted_at__isnull=True, locked_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_trashed(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_locked(self) -> bool:
        return self.locked_at is not None

    @property
    def is_read_only(self) -> bool:
        """Locked or in the Trash: the title, content and checklist can't be changed. (A takedown,
        a later sub-issue, joins them.)"""
        return self.is_locked or self.is_trashed

    def save(self, *args, **kwargs) -> None:
        self.title_text = normalize_for_search(self.title)[:TITLE_MAX]
        try:
            self.content_text = search_text(self.content, profile='extended')
        except ValueError:  # a document over the Markdown size guard (the services cap equal to it)
            self.content_text = normalize_for_search(self.content)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            fields = set(update_fields)
            if 'title' in fields:
                fields.add('title_text')
            if 'content' in fields:
                fields.add('content_text')
            kwargs['update_fields'] = fields
        super().save(*args, **kwargs)


class NoteVersion(models.Model):
    """The state of a note **after** one save (or a run of saves by the same editor within
    `VERSION_COALESCE`, which overwrite it in place). The owner can view and restore them."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    pk: uuid.UUID
    note_id: uuid.UUID
    editor_id: uuid.UUID | None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    note = cast('Note', models.ForeignKey(Note, on_delete=models.CASCADE, related_name='versions'))
    number = cast(int, models.PositiveIntegerField())
    title = cast(str, models.CharField(max_length=TITLE_MAX))
    content = cast(str, models.TextField(blank=True))
    editor = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='note_versions',
        ),
    )
    editor_kind = cast(str, models.CharField(max_length=10, choices=EditorKind))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    updated_at = cast(datetime, models.DateTimeField(default=timezone.now))

    class Meta:
        ordering = ['-number']
        constraints = [
            models.UniqueConstraint(fields=['note', 'number'], name='notes_version_number'),
        ]

    def __str__(self) -> str:
        return f'{self.note_id} v{self.number}'
