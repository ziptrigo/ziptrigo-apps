from datetime import datetime, time
from typing import ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

from ..limits import FIRST_TASK_NUMBER


class TodoAccount(models.Model):
    """A user's To-do account: their task numbering, their default due time and the billing state
    of task creation. Created lazily on first use, through
    `apps.todo.services.accounts.get_account`.

    `next_task_number` is the next number to hand out (per user, never reused). `month_key` and
    `month_task_count` count the tasks created in the user's current calendar month, for the free
    allowance. `creation_blocked_at` is set while a task exists whose price could not be paid
    (see `services.billing`).
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
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        verbose_name = 'To-do account'

    def __str__(self) -> str:
        return f'To-do account of {self.user_id}'

    @property
    def is_blocked(self) -> bool:
        return self.creation_blocked_at is not None
