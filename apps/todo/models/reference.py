import uuid
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Q

from .comment import Comment
from .task import Task


class TaskReference(models.Model):
    """`from_task` mentions `to_task` (`#1234` or a task URL) in its description (`comment` is
    null) or in one of its comments. `services.references.sync_references` keeps the rows equal to
    what the text says; a self-reference and a mention of a trashed or foreign task are not
    stored."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    from_task_id: uuid.UUID
    to_task_id: uuid.UUID
    comment_id: uuid.UUID | None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_task = cast(
        'Task',
        models.ForeignKey(Task, on_delete=models.CASCADE, related_name='references_out'),
    )
    to_task = cast(
        'Task',
        models.ForeignKey(Task, on_delete=models.CASCADE, related_name='references_in'),
    )
    comment = cast(
        'Comment | None',
        models.ForeignKey(
            Comment,
            null=True,
            blank=True,
            on_delete=models.CASCADE,
            related_name='task_references',
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['from_task', 'to_task', 'comment'], name='todo_reference_unique'
            ),
            # NULLs are distinct to a unique constraint on SQLite and Postgres alike, so the
            # description case (no comment) needs a constraint of its own.
            models.UniqueConstraint(
                fields=['from_task', 'to_task'],
                condition=Q(comment__isnull=True),
                name='todo_reference_unique_description',
            ),
        ]

    def __str__(self) -> str:
        return f'#{self.from_task.number} -> #{self.to_task.number}'
