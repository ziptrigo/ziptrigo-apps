import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

if TYPE_CHECKING:
    from django.db.models import Manager

#: The feedback text's cap (issue #82). Enforced by the form and the service, not by the database.
MAX_DESCRIPTION_LENGTH = 5000


class FeedbackStatus(models.TextChoices):
    """Where a piece of feedback is in staff's handling of it: `NEW` until someone picks it up,
    `IN_PROCESS` while being dealt with, `CLOSED` once done."""

    NEW = 'new', 'New'
    IN_PROCESS = 'in_process', 'In process'
    CLOSED = 'closed', 'Closed'


class Feedback(models.Model):
    """One message from a logged-in user through the footer's "Feedback" page (issue #82).

    `created_by` is `SET_NULL`: feedback is a support record that should outlive a hard-deleted
    account (the admin shows it as "(deleted user)").
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True, db_index=True))
    updated_at = cast(
        datetime, models.DateTimeField(auto_now=True, help_text='When the status last changed.')
    )
    created_by = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            on_delete=models.SET_NULL,
            related_name='feedback',
        ),
    )
    created_by_id: UUID | None
    description = cast(str, models.TextField(max_length=MAX_DESCRIPTION_LENGTH))
    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=FeedbackStatus.choices,
            default=FeedbackStatus.NEW,
            db_index=True,
        ),
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'feedback'

    def __str__(self) -> str:
        return f'Feedback {self.id} ({self.status})'
