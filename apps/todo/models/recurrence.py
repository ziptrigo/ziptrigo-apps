import uuid
from datetime import date, datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User


class RecurrenceMode(models.TextChoices):
    """How the next occurrence is dated."""

    FIXED = 'fixed', 'Fixed schedule'
    FROM_COMPLETION = 'from_completion', 'From completion'


class Recurrence(models.Model):
    """The repeat rule shared by the occurrences of one repeating task (issue #179).

    Each occurrence is its own `Task`, pointing here. `rrule` is an RFC 5545 `RRULE` property value
    without the `RRULE:` prefix and without `DTSTART` (`FREQ=MONTHLY;BYMONTHDAY=1`), in the
    canonical form `services.recurrence.parse_rule` produces. `anchor_date` is the date the
    *current* occurrence was scheduled for by the rule: postponing or editing an occurrence's due
    date never changes it, which is what makes "fixed" work. `occurrences_created` counts the
    occurrences made so far (the first included), which is how `COUNT` is tracked. `ended_at` is
    set when the user stops repeating or the rule is exhausted; the row is kept.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `Task.recurrence`.
    tasks: ClassVar['models.Manager']

    pk: uuid.UUID
    user_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='todo_recurrences'
        ),
    )
    rrule = cast(str, models.CharField(max_length=255))
    mode = cast(
        str, models.CharField(max_length=16, choices=RecurrenceMode, default=RecurrenceMode.FIXED)
    )
    anchor_date = cast(date, models.DateField())
    ended_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    occurrences_created = cast(int, models.PositiveIntegerField(default=1))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    def __str__(self) -> str:
        return f'{self.rrule} ({self.get_mode_display()})'  # ty: ignore[unresolved-attribute]

    @property
    def is_active(self) -> bool:
        return self.ended_at is None
