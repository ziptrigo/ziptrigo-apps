import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

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
    #: The client-side multipart part size in effect when this file's upload was started
    #: (`apps.file_transfer.services.storage.PART_SIZE_BYTES` at `add_file` time), pinned per file
    #: rather than always read live off that module-level constant -- otherwise, if the constant
    #: were ever changed while an upload was mid-flight, a resumed upload would slice the
    #: remaining bytes at the new size while S3 still has earlier parts at the old size, and the
    #: two would no longer line up into a valid part sequence.
    part_size_bytes = cast(
        int,
        models.PositiveIntegerField(
            default=67_108_864,  # 64 MiB -- must match `services.storage.PART_SIZE_BYTES`'s value.
            help_text='The multipart part size in effect when this upload was started; see '
            'apps.file_transfer.services.storage.PART_SIZE_BYTES.',
        ),
    )
    client_last_modified = cast(
        'int | None',
        models.BigIntegerField(
            null=True,
            blank=True,
            help_text="The browser File object's `lastModified` (ms since epoch) at the time "
            'this file was added, if the client supplied one. Used together with name and size '
            'to match a file the sender re-selects after a page reload back to this row, so its '
            'upload can resume instead of restarting (spec: resumable uploads) -- see '
            'apps.file_transfer.services.uploads.',
        ),
    )

    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    uploaded_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='When `uploaded` became true (a file that finished uploading). Null for a '
            'file still in progress, and for files uploaded before this column existed.',
        ),
    )

    # Who uploaded a file to a *request* (issue #306); all blank/null for a send's own files. The
    # name and email are copies taken at upload time, so they survive the account changing or
    # going away (`uploader_user` is then nulled).
    uploader_user = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='+',
            help_text='The signed-in uploader (and the owner, if they upload to their own '
            'request).',
        ),
    )
    uploader_user_id: UUID | None
    uploader_name = cast(
        str,
        models.CharField(
            max_length=100,
            blank=True,
            default='',
            db_default='',
            help_text="An anonymous uploader's optional typed name; for a signed-in uploader, a "
            'copy of the account name at upload time.',
        ),
    )
    uploader_email = cast(
        str,
        models.EmailField(
            blank=True,
            default='',
            db_default='',
            help_text="A signed-in uploader's account email at upload time.",
        ),
    )
    uploader_token_hash = cast(
        str,
        models.CharField(
            max_length=64,
            blank=True,
            default='',
            db_default='',
            help_text='An anonymous uploader: HMAC-SHA256 of a random per-browser token kept in '
            'the session (the `Transfer.draft_token_hash` pattern, `services.anon_session`), so '
            'only that browser lists and removes the file.',
        ),
    )
    uploader_ip = cast(
        'str | None',
        models.GenericIPAddressField(
            null=True,
            blank=True,
            help_text='The IP the file was uploaded from (every upload through a request). Shown '
            'to staff only; `purge_download_ips` nulls it on the same schedule as download IPs.',
        ),
    )

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['transfer'])]

    @property
    def uploader_key(self) -> str:
        """What tells one uploader from another: the account, else the browser token; `''` for a
        file that came from a send (or has no recorded uploader)."""
        if self.uploader_user_id:
            return str(self.uploader_user_id)
        return self.uploader_token_hash

    @property
    def uploader_label(self) -> str:
        """The uploader as the owner sees them next to a file: name and email for a signed-in
        uploader, the typed name (or "Someone") for an anonymous one."""
        if self.uploader_user_id or self.uploader_email:
            if self.uploader_name and self.uploader_email:
                return f'{self.uploader_name} ({self.uploader_email})'
            return self.uploader_name or self.uploader_email
        return self.uploader_name or 'Someone'

    def __str__(self) -> str:
        return f'{self.name} ({self.transfer_id})'
