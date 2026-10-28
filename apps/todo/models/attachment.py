import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

from .task import Task


class Attachment(models.Model):
    """A file attached to a task (issue #184), uploaded straight to S3 with a presigned PUT.

    The object's key is derived from the ids (`services.storage.attachment_key`), never the name.
    `uploaded` is false from `services.attachments.start_upload` until `complete_upload` has
    verified the object; only uploaded attachments are counted in `TodoAccount.stored_bytes`.
    `inline_type` is the type *sniffed from the bytes* when it is one a browser may show inline
    (images, PDF), else empty; `declared_type` is only what the browser claimed and is never used
    to decide how the file is served.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID
    task_id: uuid.UUID
    user_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = cast(
        'Task',
        models.ForeignKey(Task, on_delete=models.CASCADE, related_name='attachments'),
    )
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='todo_attachments'
        ),
    )
    filename = cast(str, models.CharField(max_length=255))
    size_bytes = cast(int, models.PositiveBigIntegerField())
    declared_type = cast(str, models.CharField(max_length=100, blank=True))
    inline_type = cast(str, models.CharField(max_length=32, blank=True))
    sha256 = cast(
        str,
        models.CharField(
            max_length=44,
            help_text='Base64 of the SHA-256, declared by the client and verified by S3 through '
            'the checksum signed into the upload URL.',
        ),
    )
    uploaded = cast(bool, models.BooleanField(default=False))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        indexes = [models.Index(fields=['task', 'created_at'], name='todo_attach_task_created')]
        ordering = ['created_at', 'id']

    def __str__(self) -> str:
        return self.filename

    @property
    def is_inline(self) -> bool:
        """Whether "Open" (shown in the browser) is offered: a sniffed inline-safe type."""
        return bool(self.inline_type)
