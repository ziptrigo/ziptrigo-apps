import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.safestring import SafeString

from apps.accounts.models import User

from .task import Task


class Comment(models.Model):
    """A Markdown comment on a task (issue #180).

    `body_text` is the normalised plain text search runs over (`services.comments` keeps it in step;
    it is empty while the comment is deleted, so a deleted comment is never found). A deleted
    comment keeps its `body` and its revisions: the owner can still read and restore them.
    `revision_count` is how many times the comment has been edited (one `CommentRevision` each).
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `CommentRevision.comment`.
    revisions: ClassVar['models.Manager']

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    task_id: uuid.UUID
    user_id: uuid.UUID
    # Set by the views on the comments a page shows: the rendered body (not stored).
    html: SafeString

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = cast(
        'Task',
        models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments'),
    )
    #: The author: always the task's owner today, kept for the future.
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='todo_comments'
        ),
    )
    body = cast(str, models.TextField())
    #: `apps.core.markdown.search_text(body)`; `''` while the comment is deleted.
    body_text = cast(str, models.TextField(blank=True, editable=False))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    edited_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    deleted_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    revision_count = cast(int, models.PositiveIntegerField(default=0))

    class Meta:
        ordering = ['created_at']
        indexes = [models.Index(fields=['task', 'created_at'])]

    def __str__(self) -> str:
        return f'Comment on #{self.task.number}'

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_edited(self) -> bool:
        return self.edited_at is not None


class CommentRevision(models.Model):
    """What a comment said before an edit replaced it. Never searchable."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    comment_id: uuid.UUID
    # Set by the views on the revisions a page shows: the rendered body (not stored).
    html: SafeString

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    comment = cast(
        'Comment',
        models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='revisions'),
    )
    #: The text before the edit that created this revision.
    body = cast(str, models.TextField())
    #: When the edit replaced it.
    replaced_at = cast(datetime, models.DateTimeField())

    class Meta:
        ordering = ['replaced_at']

    def __str__(self) -> str:
        return f'Revision of {self.comment_id}'
