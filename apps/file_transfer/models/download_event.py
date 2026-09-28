from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from .transfer import Transfer
from .transfer_file import TransferFile

if TYPE_CHECKING:
    from django.db.models import Manager


class DownloadEvent(models.Model):
    """One download of a file (or, in phase 2, the zip -- `file` is null for that). Records
    `ip` for support/abuse purposes; `purge_download_ips` nulls it out after 90 days
    (spec section 12)."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='download_events')
    transfer_id: UUID
    file = models.ForeignKey(
        TransferFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='download_events',
        help_text='Null means the zip (phase 2).',
    )
    file_id: UUID | None
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['transfer', '-created_at'])]

    def __str__(self) -> str:
        return f'Download of {self.file_id or "zip"} @ {self.created_at}'
