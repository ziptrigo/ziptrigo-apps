"""Attachments: files on a task, uploaded straight to S3 and billed by storage (issue #184).

1. `start_upload` validates the request and creates an `Attachment(uploaded=False)`, returning a
   presigned PUT URL whose signature binds the exact `Content-Length` and the client-computed
   `x-amz-checksum-sha256`: S3 refuses a body that differs in size or content. (That needs a SigV4
   client, which `services.storage.get_storage` builds; the core storage fails closed otherwise.)
2. The client PUTs the file to the URL.
3. `complete_upload` checks the object landed with the declared size, **sniffs its first bytes**
   (`inline_type`: only a PDF or an image a browser may show inline is ever served inline, and only
   by what the bytes are, never the claimed type), marks it uploaded and counts its bytes against
   the account's storage.

An attachment that is never completed is cleaned up after 24 hours (`abandon_uploads`, by the
`cleanup_uploads` job). Nothing is billed for an upload until it completes. Downloads are short-
lived presigned GET URLs (`download_url`); uploads and downloads are refused while the account is
frozen (storage unpaid).

Trashing a task keeps its attachments, still billed (like Receipts' Trash). They go with the task
when it is deleted for good or purged (`delete_for_tasks`, called by `services.tasks` inside the
transaction that deletes the task), and with the user (`services.lifecycle.delete_user_storage`).
"""

import base64
import binascii
import logging
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.services.sniff import sniff_inline_type

from ..models import Attachment, Task, TodoSettings
from .accounts import add_stored_bytes, get_account, is_frozen
from .errors import AccountFrozenError
from .storage import GET_URL_EXPIRES_SECONDS, PUT_URL_EXPIRES_SECONDS, attachment_key, get_storage

logger = logging.getLogger(__name__)

#: How long an upload may stay un-completed before `abandon_uploads` removes it.
ABANDONED_AFTER = timedelta(hours=24)

#: What a failing S3 call raises (a per-key failure of `delete_objects` is a `RuntimeError`).
STORAGE_ERRORS = (ClientError, BotoCoreError, RuntimeError)

#: How much of the object `complete_upload` reads to sniff its type.
HEAD_BYTES = 4096

FILENAME_MAX = 255
_MIB = 1024 * 1024


@dataclass(slots=True)
class UploadTicket:
    """What a client needs to upload one file: PUT the bytes to `url` with these `headers`."""

    attachment: Attachment
    url: str
    headers: dict[str, str]
    expires_in: int


def clean_filename(name: str) -> str:
    """A display-safe name: control characters stripped, `/` and `\\` replaced by `_` (a name is
    never a path), at most 255 characters, `file` when nothing is left."""
    stripped = ''.join(ch for ch in name if unicodedata.category(ch) != 'Cc')
    cleaned = stripped.replace('/', '_').replace('\\', '_').strip()
    return cleaned[:FILENAME_MAX].strip() or 'file'


def format_megabytes(size: int) -> str:
    """`50` for 50 MiB, `0.5` for half of one: the "N MB" of a limit message."""
    value = size / _MIB
    return f'{value:.0f}' if value >= 10 or value == int(value) else f'{value:.1f}'


def _validate_checksum(checksum_sha256: str) -> str:
    """The base64 SHA-256 S3 expects in `x-amz-checksum-sha256` (44 characters)."""
    try:
        if len(base64.b64decode(checksum_sha256, validate=True)) == 32:
            return checksum_sha256
    except binascii.Error, ValueError:
        pass
    raise ValidationError('The file checksum must be a base64-encoded SHA-256.')


def start_upload(
    task: Task,
    *,
    filename: str,
    size: int,
    declared_type: str,
    checksum_sha256: str,
) -> UploadTicket:
    """Register a new upload on `task` and presign its PUT. The caller must have checked that
    `task` belongs to the requesting user.

    Raises:
        AccountFrozenError: the account's storage is unpaid.
        ValidationError: the task is in the Trash or completed, the size is not between 1 byte and
            `max_attachment_bytes`, the task already has `max_attachments_per_task` attachments
            (uploads in progress count), or the checksum is malformed.
    """
    config = TodoSettings.load()
    account = get_account(task.user)
    if account.is_frozen:
        raise AccountFrozenError()

    if task.is_trashed:
        raise ValidationError('Files cannot be attached to a task in the trash.')
    if task.is_completed:
        raise ValidationError('Files cannot be attached to a completed task.')
    if size < 1:
        raise ValidationError('The file is empty.')
    if size > config.max_attachment_bytes:
        raise ValidationError(
            f'The file is too large (max {format_megabytes(config.max_attachment_bytes)} MB).'
        )
    checksum_sha256 = _validate_checksum(checksum_sha256)

    attachment = Attachment(
        task=task,
        user=task.user,
        filename=clean_filename(filename),
        size_bytes=size,
        declared_type=(declared_type or '').split(';')[0].strip().lower()[:100],
        sha256=checksum_sha256,
        uploaded=False,
    )
    with transaction.atomic():
        # The task row is the lock the cap is counted under, so two starts can't both take the last
        # slot; the state is re-read under it (it may have been trashed or completed meanwhile).
        locked = Task.objects.select_for_update().get(pk=task.pk)
        if locked.is_trashed or locked.is_completed:
            raise ValidationError('Files cannot be attached to this task any more.')
        if Attachment.objects.filter(task=locked).count() >= config.max_attachments_per_task:
            raise ValidationError(f'A task can have up to {config.max_attachments_per_task} files.')
        attachment.save()
    url = get_storage().presigned_put_url(
        attachment_key(task.user_id, attachment.pk),
        content_length=size,
        checksum_sha256=checksum_sha256,
    )
    return UploadTicket(
        attachment=attachment,
        url=url,
        headers={'x-amz-checksum-sha256': checksum_sha256},
        expires_in=PUT_URL_EXPIRES_SECONDS,
    )


def _discard(attachment: Attachment) -> None:
    """Delete a failed upload: its object (if any) and its row. Nothing was ever counted."""
    try:
        get_storage().delete_object(attachment_key(attachment.user_id, attachment.pk))
    except ClientError, BotoCoreError:
        logger.exception('Could not delete the rejected upload %s', attachment.pk)
    Attachment.objects.filter(pk=attachment.pk, uploaded=False).delete()


def complete_upload(attachment: Attachment) -> Attachment:
    """Finish an upload after the client's PUT. Idempotent: completing a completed attachment
    returns it unchanged. The caller must have checked that `attachment` belongs to the requesting
    user.

    Raises:
        ValidationError: the object isn't there (yet), or it is not the size declared (the object
            and the row are deleted).
    """
    if attachment.uploaded:
        return attachment
    storage = get_storage()
    key = attachment_key(attachment.user_id, attachment.pk)

    try:
        info = storage.head_object(key)
    except (ClientError, BotoCoreError) as exc:
        raise ValidationError('The file has not been uploaded.') from exc
    if info.size != attachment.size_bytes or (
        attachment.sha256 and info.checksum_sha256 and info.checksum_sha256 != attachment.sha256
    ):
        _discard(attachment)
        raise ValidationError('The upload did not complete correctly.')

    try:
        stream = storage.get_object_stream(key)
        try:
            head = stream.read(HEAD_BYTES)
        finally:
            stream.close()
    except (ClientError, BotoCoreError) as exc:
        raise ValidationError('The file could not be read back; try again.') from exc
    inline_type = sniff_inline_type(head) or ''

    with transaction.atomic():
        won = Attachment.objects.filter(pk=attachment.pk, uploaded=False).update(
            uploaded=True, inline_type=inline_type
        )
        if won:
            add_stored_bytes(attachment.user_id, attachment.size_bytes)
    attachment.refresh_from_db()
    return attachment


def abandon_uploads(*, older_than: timedelta = ABANDONED_AFTER) -> int:
    """Remove uploads started but never completed within `older_than`: their objects (if any
    landed) and their rows. One bad row is logged and skipped. Returns how many were removed."""
    cutoff = timezone.now() - older_than
    removed = 0
    for attachment in Attachment.objects.filter(uploaded=False, created_at__lte=cutoff):
        try:
            get_storage().delete_object(attachment_key(attachment.user_id, attachment.pk))
            removed += Attachment.objects.filter(pk=attachment.pk, uploaded=False).delete()[0]
        except Exception:
            logger.exception('Failed to clean up abandoned upload %s', attachment.pk)
    return removed


def delete_attachment(attachment: Attachment) -> None:
    """Delete one attachment: its object, then its row, subtracting its bytes from the account's
    storage. A storage error raises and leaves the attachment as it was. Deleting an attachment
    that is already gone is a no-op, and the bytes are subtracted only by the call that deleted
    the row."""
    get_storage().delete_object(attachment_key(attachment.user_id, attachment.pk))
    with transaction.atomic():
        current = Attachment.objects.select_for_update().filter(pk=attachment.pk).first()
        if current is None:
            return
        current.delete()
        if current.uploaded:
            add_stored_bytes(current.user_id, -current.size_bytes)


def delete_for_tasks(task_ids: Iterable[UUID]) -> int:
    """Delete every attachment of the given tasks: their objects first (one `delete_objects`; a
    per-key S3 error raises after deleting the rest, leaving the rows and the bytes for the next
    try), then the rows, subtracting the uploaded ones' bytes from each account.

    Called by `services.tasks` just before it deletes the tasks (a task trashed and then deleted
    forever, or purged), inside the transaction that deletes them. Returns how many were deleted.
    """
    ids = list(task_ids)
    if not ids:
        return 0
    rows = list(Attachment.objects.filter(task_id__in=ids).only('id', 'user_id'))
    if not rows:
        return 0
    get_storage().delete_objects([attachment_key(row.user_id, row.pk) for row in rows])
    with transaction.atomic():
        # Re-read under a lock for what to subtract: an upload completed since the read above is
        # counted (and its object, deleted just now, is gone with it).
        locked = list(Attachment.objects.select_for_update().filter(task_id__in=ids))
        freed: dict[UUID, int] = {}
        for row in locked:
            if row.uploaded:
                freed[row.user_id] = freed.get(row.user_id, 0) + row.size_bytes
        Attachment.objects.filter(pk__in=[row.pk for row in locked]).delete()
        for user_id, size in freed.items():
            add_stored_bytes(user_id, -size)
    return len(locked)


def download_url(attachment: Attachment, *, inline: bool = False) -> str:
    """A short-lived URL for the file. `inline=True` shows it in the browser, but only when its
    bytes were sniffed as an inline-safe type (an image or a PDF) at upload; anything else is
    always a download. Never decided by the type the browser declared.

    Raises:
        AccountFrozenError: the account's storage is unpaid.
        ValidationError: the upload never completed.
    """
    if is_frozen(attachment.user_id):
        raise AccountFrozenError()
    if not attachment.uploaded:
        raise ValidationError('There is no file for this attachment.')
    show_inline = inline and bool(attachment.inline_type)
    return get_storage().presigned_get_url(
        attachment_key(attachment.user_id, attachment.pk),
        attachment.filename or 'file',
        GET_URL_EXPIRES_SECONDS,
        disposition='inline' if show_inline else 'attachment',
        content_type=attachment.inline_type or None,
    )
