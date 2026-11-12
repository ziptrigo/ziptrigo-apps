import uuid
from datetime import datetime
from typing import Any, ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone

from apps.accounts.models import User

from .task import Task


class EventKind(models.TextChoices):
    """What happened to the task (issue #274). The `data` each kind carries is documented in
    `services.events`; `services.event_text` turns the pair into a sentence."""

    CREATED = 'created', 'Created'
    STATE = 'state', 'State changed'
    CLOSED = 'closed', 'Closed'
    REOPENED = 'reopened', 'Reopened'
    RESOLUTION = 'resolution', 'Resolution changed'
    TITLE = 'title', 'Title changed'
    DESCRIPTION = 'description', 'Description edited'
    CHECKLIST = 'checklist', 'Checklist item'
    PRIORITY = 'priority', 'Priority changed'
    DUE = 'due', 'Due date changed'
    LABELS = 'labels', 'Labels changed'
    REPEAT = 'repeat', 'Repeat changed'
    BLOCKER_ADDED = 'blocker_added', 'Blocker added'
    BLOCKER_REMOVED = 'blocker_removed', 'Blocker removed'
    BLOCKED = 'blocked', 'Blocked'
    UNBLOCKED = 'unblocked', 'Unblocked'
    SUBTASK_ADDED = 'subtask_added', 'Subtask added'
    SUBTASK_REMOVED = 'subtask_removed', 'Subtask moved out'
    SUBTASK_CLOSED = 'subtask_closed', 'Subtask closed'
    MOVED = 'moved', 'Moved'
    ATTACHMENT_ADDED = 'attachment_added', 'File attached'
    ATTACHMENT_REMOVED = 'attachment_removed', 'File removed'
    TRASHED = 'trashed', 'Moved to the Trash'
    RESTORED = 'restored', 'Restored'


class EventChannel(models.TextChoices):
    """Which entry point made the change: a browser, the JWT API, an email, the phone's
    notification buttons, or the system itself (a job, the shell)."""

    WEB = 'web', 'Web'
    API = 'api', 'API'
    EMAIL = 'email', 'Email'
    PUSH = 'push', 'Push notification'
    SYSTEM = 'system', 'System'


class TaskEvent(models.Model):
    """One entry of a task's activity log: what changed, when, and whether it was automatic.

    Written only by `services.events` (inside the transaction of the change it records), never
    edited, shown on the task page on request. `data` holds the values themselves (old and new
    titles, numbers, label names, file names), so an entry still reads correctly after the task,
    label or file it mentions is gone. It lives as long as its task: it cascades with the task
    (deleted for good, purged) and the user. It is not searched.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    pk: uuid.UUID
    task_id: uuid.UUID
    user_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = cast(
        'Task',
        models.ForeignKey(
            Task,
            on_delete=models.CASCADE,
            related_name='events',
            # No constraint, no single-column index: an insert into a table with a foreign key
            # takes `FOR KEY SHARE` on the referenced row on PostgreSQL, which conflicts with the
            # `FOR UPDATE` the services hold on tasks and could close a deadlock cycle. Django's
            # collector still cascades; `(task, created_at)` below covers lookups by task.
            db_constraint=False,
            db_index=False,
        ),
    )
    #: The task's owner (the person the change was made for), kept for the admin's filters.
    user = cast(
        'User',
        models.ForeignKey(
            User,
            on_delete=models.CASCADE,
            related_name='todo_task_events',
            db_constraint=False,  # see `task`
        ),
    )
    kind = cast(str, models.CharField(max_length=24, choices=EventKind.choices))
    data = cast(dict[str, Any], models.JSONField(default=dict, db_default={}, blank=True))
    #: True for a change nobody asked for in that moment: an automatic unblock, the next
    #: occurrence of a repeating task, a task made from an email, a parent closing its subtasks.
    automatic = cast(bool, models.BooleanField(default=False, db_default=False))
    channel = cast(
        str,
        models.CharField(
            max_length=12,
            choices=EventChannel.choices,
            default=EventChannel.SYSTEM,
            db_default=EventChannel.SYSTEM,
        ),
    )
    created_at = cast(datetime, models.DateTimeField(default=timezone.now))

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['task', 'created_at'], name='todo_event_task_created')]

    def __str__(self) -> str:
        return f'{self.kind} on #{self.task.number}'
