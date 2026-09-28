import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from .transfer import Transfer

if TYPE_CHECKING:
    from django.db.models import Manager


class TransferFile(models.Model):
    """One file within a `Transfer`, stored in S3 at `storage_key`
    (`transfers/<transfer_id>/<file_id>/<filename>`, see `apps.file_transfer.services.storage`).

    `upload_id` is the S3 multipart upload id while the upload is in progress; it's cleared (but
    kept for a short time so `cleanup_drafts` can abort it) once `uploaded` is set.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='files')
    transfer_id: UUID

    name = cast(str, models.CharField(max_length=255))
    size = cast(int, models.PositiveBigIntegerField())
    storage_key = cast(str, models.CharField(max_length=512))
    checksum = cast(
        str,
        models.CharField(
            max_length=128, blank=True, default='', help_text='SHA-256 checksum from S3, base64.'
        ),
    )
    upload_id = cast(str, models.CharField(max_length=255, blank=True, default=''))
    uploaded = cast(bool, models.BooleanField(default=False))

    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['transfer'])]

    def __str__(self) -> str:
        return f'{self.name} ({self.transfer_id})'
