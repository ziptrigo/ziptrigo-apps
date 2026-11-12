import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import F, Q

from .task import Task


class TaskBlocker(models.Model):
    """`blocker` blocks `blocked` (issue #273): "Blocked by" on the blocked task, "Blocks" on the
    other. A lasting link between two of the same user's tasks, kept when either closes (only the
    Trash being emptied, or the user, removes it: both cascade). The owner is implied by the tasks;
    `services.blockers` refuses a link between two users, a task and itself and a cycle, and is the
    only place that writes the rows."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    blocker_id: uuid.UUID
    blocked_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    blocker = cast(
        'Task',
        models.ForeignKey(Task, on_delete=models.CASCADE, related_name='blocks_links'),
    )
    blocked = cast(
        'Task',
        models.ForeignKey(Task, on_delete=models.CASCADE, related_name='blocked_by_links'),
    )
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['blocker', 'blocked'], name='todo_blocker_unique'),
            models.CheckConstraint(
                condition=~Q(blocker=F('blocked')), name='todo_blocker_not_self'
            ),
        ]

    def __str__(self) -> str:
        return f'#{self.blocker.number} blocks #{self.blocked.number}'
