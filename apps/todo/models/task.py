import uuid
from datetime import date, datetime, time
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models

from apps.accounts.models import User
from apps.core.markdown import normalize_for_search, search_text

from ..limits import TITLE_MAX
from .label import Label
from .recurrence import Recurrence


class TaskSource(models.TextChoices):
    """Where a task came from. Billing counts every source alike."""

    WEB = 'web', 'Web'
    API = 'api', 'API'
    EMAIL = 'email', 'Email'
    RECURRENCE = 'recurrence', 'Repeating task'


class Priority(models.IntegerChoices):
    """1 is the highest."""

    NONE = 0, 'None'
    P1 = 1, 'Priority 1'
    P2 = 2, 'Priority 2'
    P3 = 3, 'Priority 3'


class Task(models.Model):
    """One task, or a subtask (one level deep: a subtask's parent has no parent).

    `number` is the user's own, from 1000, never reused: `#1234` and `/todo/1234/` name the task.
    The due date and time are a local date and wall-clock time, not an instant:
    `services.dates.due_at` turns them into one with the user's time zone. Trash is `deleted_at`,
    not a status. `title_text` and `description_text` are the normalised copies search runs over
    (`save()` keeps them in step).
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `parent`.
    subtasks: ClassVar['models.Manager']
    #: The reverse accessors of `Comment.task` and `TaskReference.from_task` / `to_task`.
    comments: ClassVar['models.Manager']
    references_out: ClassVar['models.Manager']
    references_in: ClassVar['models.Manager']
    #: The reverse accessors of `Reminder.task` and `ReminderDelivery.task`.
    reminders: ClassVar['models.Manager']
    reminder_deliveries: ClassVar['models.Manager']
    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    user_id: uuid.UUID
    parent_id: uuid.UUID | None
    recurrence_id: uuid.UUID | None
    # Set by `services.dates.with_subtask_counts` on the tasks a list shows (not stored).
    subtask_total: int
    subtask_done: int
    # Set by `services.dates.with_open_occurrences` (not stored).
    open_occurrences: int
    subtask_open: int
    # Set by `services.comments.with_comment_counts` on the tasks a list shows (not stored).
    comment_count: int
    # Set by `services.reminders.with_reminder_flags` on the tasks a list shows (not stored).
    has_reminders: bool

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='todo_tasks'
        ),
    )
    number = cast(int, models.PositiveIntegerField())
    parent = cast(
        'Task | None',
        models.ForeignKey(
            'self', null=True, blank=True, on_delete=models.CASCADE, related_name='subtasks'
        ),
    )

    title = cast(str, models.CharField(max_length=TITLE_MAX))
    description = cast(str, models.TextField(blank=True))
    #: `apps.core.markdown.search_text(description)`.
    description_text = cast(str, models.TextField(blank=True, editable=False))
    #: `apps.core.markdown.normalize_for_search(title)`.
    title_text = cast(str, models.CharField(max_length=TITLE_MAX, blank=True, editable=False))

    priority = cast(int, models.PositiveSmallIntegerField(choices=Priority, default=Priority.NONE))
    due_date = cast(date | None, models.DateField(null=True, blank=True))
    due_time = cast(time | None, models.TimeField(null=True, blank=True))

    completed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    deleted_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    #: Manual order among a parent's subtasks (`services.tasks.reorder`, scope `subtasks`).
    position = cast(int, models.IntegerField(default=0))
    #: Manual order of the Today list, among the tasks due today (scope `today`). A separate column
    #: from `position`, so dragging in one list never puts the other into manual order.
    today_position = cast(int, models.IntegerField(default=0))
    source = cast(str, models.CharField(max_length=12, choices=TaskSource, default=TaskSource.WEB))
    charged_credits = cast(
        int,
        models.PositiveIntegerField(
            default=0, help_text='What this task cost to create (also while unpaid).'
        ),
    )
    unpaid_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='Set while the price of this task is still owed (see services.billing).',
        ),
    )
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    updated_at = cast(datetime, models.DateTimeField(auto_now=True))

    #: The repeat rule this task is an occurrence of (shared by all of them; `None` for a task that
    #: never repeated). Stopping keeps the link and sets `Recurrence.ended_at`.
    recurrence = cast(
        'Recurrence | None',
        models.ForeignKey(
            Recurrence,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='tasks',
        ),
    )

    #: The `Recurrence.anchor_date` this occurrence was scheduled for: set when it is created as an
    #: occurrence, and again when a rule change re-anchors it. Completing it creates the next
    #: occurrence only while this is still the recurrence's anchor, so an occurrence spawns at most
    #: one (completing it again after an uncomplete, or an older open one, creates nothing).
    recurrence_anchor = cast(date | None, models.DateField(null=True, blank=True))

    labels = cast(
        'models.Manager',
        models.ManyToManyField(Label, blank=True, related_name='tasks'),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'number'], name='todo_task_user_number'),
        ]
        indexes = [
            models.Index(fields=['user', 'deleted_at', 'completed_at', 'due_date']),
            models.Index(fields=['user', 'parent']),
        ]

    def __str__(self) -> str:
        return f'#{self.number} {self.title}'

    @property
    def is_open(self) -> bool:
        return self.completed_at is None and self.deleted_at is None

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None

    @property
    def is_trashed(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_unpaid(self) -> bool:
        return self.unpaid_at is not None

    @property
    def active_recurrence(self) -> Recurrence | None:
        """The repeat rule while it is still running (not stopped or exhausted), else `None`."""
        if self.recurrence_id is None:
            return None
        recurrence = self.recurrence
        return recurrence if recurrence is not None and recurrence.is_active else None

    @property
    def offers_stop_repeating(self) -> bool:
        """Whether trashing this task should offer "Also stop repeating": it is open, its repeat is
        running and it is the only open occurrence (trashing one of several leaves the repeat
        with another occurrence to carry on from). Reads the `open_occurrences` annotation when the
        task has it, else counts."""
        if self.is_completed or self.active_recurrence is None:
            return False
        count = getattr(self, 'open_occurrences', None)
        if count is None:
            count = Task.objects.filter(
                recurrence_id=self.recurrence_id,
                completed_at__isnull=True,
                deleted_at__isnull=True,
            ).count()
        return count <= 1

    def is_overdue(self, today_local: date) -> bool:
        """Open, with a due date before `today_local` (the user's today)."""
        return self.is_open and self.due_date is not None and self.due_date < today_local

    def clean(self) -> None:
        super().clean()
        if self.parent_id is not None:
            parent = self.parent
            if parent is None or parent.parent_id is not None or parent.pk == self.pk:
                raise ValidationError({'parent': 'A subtask cannot have subtasks.'})
        if self.due_time is not None and self.due_date is None:
            raise ValidationError({'due_time': 'A time needs a date.'})
        if self.recurrence_id is not None and self.parent_id is not None:
            raise ValidationError({'recurrence': 'A subtask cannot repeat.'})

    def save(self, *args, **kwargs) -> None:
        self.title_text = normalize_for_search(self.title)[:TITLE_MAX]
        try:
            self.description_text = search_text(self.description)
        except ValueError:  # a document over the Markdown size guard (the services cap far below)
            self.description_text = normalize_for_search(self.description)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            fields = set(update_fields)
            if 'title' in fields:
                fields.add('title_text')
            if 'description' in fields:
                fields.add('description_text')
            kwargs['update_fields'] = fields
        super().save(*args, **kwargs)
