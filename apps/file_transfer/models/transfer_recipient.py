import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from .transfer import Transfer

if TYPE_CHECKING:
    from django.db.models import Manager


class TransferRecipient(models.Model):
    """One recipient email address for a `Transfer`. Deduplicated per transfer at the service
    layer (`apps.file_transfer.services.transfers`)."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = cast(
        Transfer, models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='recipients')
    )
    transfer_id: UUID

    email = cast(str, models.EmailField())
    last_sent_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        ordering = ['email']
        constraints = [
            models.UniqueConstraint(fields=['transfer', 'email'], name='unique_transfer_recipient')
        ]

    def __str__(self) -> str:
        return self.email
