import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

from ..limits import MAX_REMINDER_MINUTES
from .task import Task


class ReminderChannel(models.TextChoices):
    """Where a reminder is delivered."""

    IN_APP = 'in_app', 'In-app'
    EMAIL = 'email', 'Email'
    BOTH = 'both', 'Both'


class DeliveryChannel(models.TextChoices):
    """The channel one delivery actually used (`both` is two deliveries)."""

    IN_APP = 'in_app', 'In-app'
    EMAIL = 'email', 'Email'


class Reminder(models.Model):
    """A reminder on a task: `minutes_before` its due time (a date-only task is due at the owner's
    default due time), delivered in-app, by email or both (issue #182).

    `fire_at` is the UTC instant it is due, recomputed by `services.reminders.reschedule` whenever
    the date, the time, the default due time or the owner's time zone changes. `sent_at` is null
    until the job has delivered it or decided not to (`skipped`: it was too old to deliver). A
    user-initiated change of the due date arms a sent reminder again (`sent_at` null, `skipped`
    false) when its new time is ahead. `fire_token` is regenerated each time the reminder is
    claimed for delivery, so the email task can tell one firing from the next; `failures` counts
    the deliveries that raised since it was last armed (it keeps the log quiet).
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    task_id: uuid.UUID
    # Set by `services.reminders.task_reminders` on the reminders the task page shows (not stored).
    offset_text: str
    amount: int
    unit: str

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = cast(
        'Task',
        models.ForeignKey(Task, on_delete=models.CASCADE, related_name='reminders'),
    )
    minutes_before = cast(int, models.PositiveIntegerField())
    channel = cast(
        str,
        models.CharField(max_length=8, choices=ReminderChannel, default=ReminderChannel.IN_APP),
    )
    fire_at = cast(datetime, models.DateTimeField())
    sent_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    skipped = cast(bool, models.BooleanField(default=False, db_default=False))
    fire_token = cast(uuid.UUID, models.UUIDField(default=uuid.uuid4, editable=False))
    failures = cast(int, models.PositiveSmallIntegerField(default=0, db_default=0))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['minutes_before', 'created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'minutes_before'], name='todo_reminder_task_minutes'
            ),
            models.CheckConstraint(
                condition=models.Q(minutes_before__lte=MAX_REMINDER_MINUTES),
                name='todo_reminder_minutes_max',
            ),
        ]
        # The job only ever looks for unsent reminders by time (`deliver_due`, `sweep_stale`): a
        # partial index stays as small as the reminders waiting, however many have been sent.
        indexes = [
            models.Index(
                fields=['fire_at'],
                condition=models.Q(sent_at__isnull=True),
                name='todo_reminder_unsent_fire',
            )
        ]

    def __str__(self) -> str:
        return f'Reminder {self.minutes_before} min before #{self.task.number}'

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None


class ReminderDelivery(models.Model):
    """One delivery of a reminder through one channel: the reminder history. `task_number` and
    `task_title` are copied at delivery, so the history survives a purged task; it is independent
    of the notification purge (`notification_id` is only the core notification's id when in-app).
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: uuid.UUID
    user_id: uuid.UUID
    task_id: uuid.UUID | None
    reminder_id: uuid.UUID | None

    #: `note` when an email was skipped because the address is not confirmed.
    NOTE_UNCONFIRMED = 'unconfirmed'
    #: `note` when every email backend failed, or the email could not be queued.
    NOTE_FAILED = 'failed'
    #: `note` when the reminder was not delivered at all: its time had passed (a late edit, or a
    #: job that was down) before anything could deliver it.
    NOTE_SKIPPED = 'skipped'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reminder = cast(
        'Reminder | None',
        models.ForeignKey(
            Reminder,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='deliveries',
        ),
    )
    task = cast(
        'Task | None',
        models.ForeignKey(
            Task,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='reminder_deliveries',
        ),
    )
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            related_name='todo_reminder_deliveries',
        ),
    )
    task_number = cast(int, models.PositiveIntegerField())
    task_title = cast(str, models.CharField(max_length=500))
    channel = cast(str, models.CharField(max_length=8, choices=DeliveryChannel))
    note = cast(str, models.CharField(max_length=32, blank=True))
    sent_at = cast(datetime, models.DateTimeField())
    fire_at = cast(datetime, models.DateTimeField())
    notification_id = cast(uuid.UUID | None, models.UUIDField(null=True, blank=True))
    #: The reminder's `fire_token` at the firing this row belongs to (null for a skipped one):
    #: what the email task dedupes on.
    fire_token = cast(uuid.UUID | None, models.UUIDField(null=True, blank=True))

    class Meta:
        ordering = ['-sent_at']
        indexes = [models.Index(fields=['user', '-sent_at'])]

    def __str__(self) -> str:
        return f'{DeliveryChannel(self.channel).label} reminder for #{self.task_number}'
