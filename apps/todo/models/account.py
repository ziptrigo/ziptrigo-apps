from datetime import datetime, time
from decimal import Decimal
from typing import ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MaxValueValidator
from django.db import models

from apps.accounts.models import User

from ..limits import FIRST_TASK_NUMBER, MAX_REMINDER_MINUTES
from .reminder import ReminderChannel


class TodoAccount(models.Model):
    """A user's To-do account: their task numbering, their default due time and the billing state
    of task creation. Created lazily on first use, through
    `apps.todo.services.accounts.get_account`.

    `next_task_number` is the next number to hand out (per user, never reused). `month_key` and
    `month_task_count` count the tasks created in the user's current calendar month, for the free
    allowance. `creation_blocked_at` is set while a task exists whose price could not be paid
    (see `services.billing`).

    Attachment storage is billed per account (issue #184, `services.metering`), with the same
    four columns as `receipts.ReceiptsAccount`: `stored_bytes` x the shared storage price once a
    day (`accrued` carries the fraction), and `frozen_at` set when the balance can't cover it.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    user = cast(
        'User',
        models.OneToOneField(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            primary_key=True,
            related_name='todo_account',
        ),
    )
    user_id: UUID

    next_task_number = cast(
        int, models.PositiveIntegerField(default=FIRST_TASK_NUMBER, editable=False)
    )
    default_due_time = cast(
        time,
        models.TimeField(
            default=time(9, 0),
            help_text='The time a task with a date and no time is treated as due, for reminders '
            'and ordering.',
        ),
    )
    default_reminder_minutes = cast(
        int | None,
        models.PositiveIntegerField(
            null=True,
            blank=True,
            default=5,
            db_default=5,
            validators=[MaxValueValidator(MAX_REMINDER_MINUTES)],
            help_text='Minutes before the due time of the reminder a new task with a date gets; '
            'empty means new tasks get no reminder.',
        ),
    )
    default_reminder_channel = cast(
        str,
        models.CharField(
            max_length=8,
            choices=ReminderChannel,
            default=ReminderChannel.IN_APP,
            db_default=ReminderChannel.IN_APP,
            help_text='Where the default reminder is delivered.',
        ),
    )
    month_key = cast(
        str,
        models.CharField(
            max_length=7,
            blank=True,
            help_text="YYYY-MM, in the user's time zone, of the month month_task_count counts.",
        ),
    )
    month_task_count = cast(int, models.PositiveIntegerField(default=0))
    creation_blocked_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='Set while a task exists whose price could not be paid; new tasks are '
            'refused until a top-up settles it.',
        ),
    )
    stored_bytes = cast(
        int,
        models.PositiveBigIntegerField(
            default=0,
            help_text='Everything stored for this account and billed: the uploaded attachments '
            'of every task, trashed ones included until they are purged.',
        ),
    )
    last_billed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    accrued = cast(
        Decimal,
        models.DecimalField(
            max_digits=14,
            decimal_places=6,
            default=Decimal('0'),
            help_text='Fractional credits owed but not yet spent (billing.spend_credits only '
            'takes whole credits); see apps.todo.services.metering.',
        ),
    )
    billed_days = cast(int, models.PositiveIntegerField(default=0))
    frozen_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='Set when storage could not be paid for; cleared on top-up.',
        ),
    )
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        verbose_name = 'To-do account'

    def __str__(self) -> str:
        return f'To-do account of {self.user_id}'

    @property
    def is_blocked(self) -> bool:
        return self.creation_blocked_at is not None

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None
