"""Attachments: files on a task, uploaded straight to S3 and billed by storage (issue #184).

1. `start_upload` validates the request and creates an `Attachment(uploaded=False)`, returning a
   presigned PUT URL whose signature binds the exact `Content-Length` and the client-computed
   `x-amz-checksum-sha256`: S3 refuses a body that differs in size or content. (That needs a SigV4
   client, which `services.storage.get_storage` builds; the core storage fails closed otherwise.)
2. The client PUTs the file to the URL.
3. `complete_upload` checks the object landed with the declared size, **reads it back and compares
   its SHA-256 with the declared one -- always, not only when S3 echoes a checksum** (streamed in
   chunks, so memory stays bounded for a 50 MiB file; a failed read-back is a failure, not a pass),
   **sniffs its first bytes** (`inline_type`: only a PDF or an image a browser may show inline is
   ever served inline, and only by what the bytes are, never the claimed type), marks it uploaded
   and counts its bytes against the account's storage. It refuses (and deletes the object) when the
   task was trashed or deleted since the upload began.

An upload the user cancels (`cancel_upload`) is removed at once, so it stops counting toward the
per-task cap; one that is never completed is cleaned up after 24 hours (`abandon_uploads`, by the
`cleanup_uploads` job). Both claim the row first (a conditional delete on `uploaded=False`) and
delete the object only after winning, so a `complete` that arrives late finds no row. Nothing is
billed for an upload until it completes. Downloads are short-
lived presigned GET URLs (`download_url`); uploads and downloads are refused while the account is
frozen (storage unpaid).

**Known window** (accepted): an upload still in flight when its task is deleted for good can
land its object *after* `delete_for_tasks` ran. Object keys are per attachment, not per task, so
there is no task prefix to sweep. The object is removed when the browser reports the finished PUT
(`complete` finds no row and calls `discard_orphan_object`), and the deletes above are repeated once
the rows are gone, which narrows it; what remains is a PUT landing after that with a browser that
never calls `complete` (the tab closed mid-upload). That object is never billed, and goes with the
user's prefix when the account is deleted.

Trashing a task keeps its attachments, still billed (like Receipts' Trash). They go with the task
when it is deleted for good or purged (`delete_for_tasks`, called by `services.tasks` inside the
transaction that deletes the task), and with the user (`services.lifecycle.delete_user_storage`).
"""

import base64
import binascii
import hashlib
import logging
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.services.sniff import SNIFF_BYTES, sniff_inline_type

from ..models import Attachment, Task, TodoSettings
from .accounts import add_stored_bytes, get_account, is_frozen
from .errors import AccountFrozenError
from .storage import GET_URL_EXPIRES_SECONDS, PUT_URL_EXPIRES_SECONDS, attachment_key, get_storage

logger = logging.getLogger(__name__)

#: How long an upload may stay un-completed before `abandon_uploads` removes it.
ABANDONED_AFTER = timedelta(hours=24)

#: What a failing S3 call raises (a per-key failure of `delete_objects` is a `RuntimeError`).
STORAGE_ERRORS = (ClientError, BotoCoreError, RuntimeError)

#: What reading an object back can raise: a failing S3 call, or the connection dropping mid-body.
READ_ERRORS = (ClientError, BotoCoreError, OSError)

#: How much of the object `complete_upload` reads at a time when verifying it.
READ_CHUNK_BYTES = 1024 * 1024

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
    get_account(task.user)

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
        # Checked under the lock, with the state of the task: an account frozen since the caller
        # looked takes no upload.
        if is_frozen(locked.user_id):
            raise AccountFrozenError()
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


def _delete_object(user_id: object, attachment_id: object) -> None:
    """Delete the attachment's object, logging (not raising) a storage error: callers have already
    removed the row, and a lost delete only leaves an unreferenced object."""
    key = attachment_key(user_id, attachment_id)
    try:
        get_storage().delete_object(key)
    except ClientError, BotoCoreError:
        logger.exception('Could not delete the object %s of a removed upload', key)


def _discard(attachment: Attachment) -> None:
    """Remove an upload that cannot be completed: the row first (a conditional delete on
    `uploaded=False`, the claim), then its object, so a `complete` racing this finds no row. If the
    row is still there and uploaded, another call completed it and its object is not ours to delete.
    Nothing was ever counted."""
    Attachment.objects.filter(pk=attachment.pk, uploaded=False).delete()
    if not Attachment.objects.filter(pk=attachment.pk).exists():
        _delete_object(attachment.user_id, attachment.pk)


def discard_orphan_object(user_id: object, attachment_id: object) -> None:
    """Delete the object of an upload whose row no longer exists (the task was deleted, or the
    upload cancelled or abandoned, while the browser's PUT was in flight). The caller passes the
    requesting user's id, so this can only touch that user's own prefix; it does nothing while any
    row has that id."""
    if not Attachment.objects.filter(pk=attachment_id).exists():
        _delete_object(user_id, attachment_id)


def _read_back(storage, key: str) -> tuple[int, str, bytes]:
    """Stream the object once: its size, base64 SHA-256 and first `SNIFF_BYTES` bytes. Memory
    stays at one chunk, whatever the size."""
    digest = hashlib.sha256()
    total = 0
    head = b''
    stream = storage.get_object_stream(key)
    try:
        while chunk := stream.read(READ_CHUNK_BYTES):
            if len(head) < SNIFF_BYTES:
                head += chunk[: SNIFF_BYTES - len(head)]
            digest.update(chunk)
            total += len(chunk)
    finally:
        stream.close()
    return total, base64.b64encode(digest.digest()).decode(), head


def complete_upload(attachment: Attachment) -> Attachment:
    """Finish an upload after the client's PUT. Idempotent: completing a completed attachment
    returns it unchanged. The caller must have checked that `attachment` belongs to the requesting
    user.

    Raises:
        ValidationError: the object isn't there (yet) or can't be read back (the row is kept, try
            again); it is not the size or SHA-256 declared (the object and the row are deleted);
            or the task is in the Trash (the object and the row are deleted).
        Attachment.DoesNotExist: the row is gone (the task was deleted, or the upload cancelled or
            abandoned, meanwhile); any object found is deleted.
    """
    if attachment.uploaded:
        return attachment
    storage = get_storage()
    key = attachment_key(attachment.user_id, attachment.pk)

    try:
        info = storage.head_object(key)
    except (ClientError, BotoCoreError) as exc:
        if not Attachment.objects.filter(pk=attachment.pk).exists():
            raise Attachment.DoesNotExist('This upload no longer exists.') from exc
        raise ValidationError('The file has not been uploaded.') from exc
    if info.size != attachment.size_bytes:
        _discard(attachment)
        raise ValidationError('The upload did not complete correctly.')

    # The comparison never depends on S3 echoing a checksum: the bytes are hashed here.
    try:
        size, digest, head = _read_back(storage, key)
    except READ_ERRORS as exc:
        raise ValidationError('The file could not be read back; try again.') from exc
    if size != attachment.size_bytes or digest != attachment.sha256:
        _discard(attachment)
        raise ValidationError('The upload did not complete correctly.')
    inline_type = sniff_inline_type(head) or ''

    with transaction.atomic():
        task = Task.objects.filter(pk=attachment.task_id).only('deleted_at').first()
        trashed = task is not None and task.is_trashed
        won = 0
        if task is not None and not trashed:
            won = Attachment.objects.filter(pk=attachment.pk, uploaded=False).update(
                uploaded=True, inline_type=inline_type
            )
            if won:
                add_stored_bytes(attachment.user_id, attachment.size_bytes)
    if won:
        attachment.uploaded = True
        attachment.inline_type = inline_type
        return attachment
    if trashed:
        _discard(attachment)
        raise ValidationError('Files cannot be attached to a task in the trash.')
    current = Attachment.objects.filter(pk=attachment.pk).first()
    if current is None:  # deleted meanwhile: don't leave its object behind
        _delete_object(attachment.user_id, attachment.pk)
        raise Attachment.DoesNotExist('This upload no longer exists.')
    return current  # completed by a concurrent call


def cancel_upload(attachment: Attachment) -> None:
    """Remove a pending upload the user gave up on: its row (so it stops counting toward the
    task's cap) and then its object, if the PUT landed. Raises `ValidationError` for an attachment
    that is already uploaded (delete it instead)."""
    if attachment.uploaded:
        raise ValidationError('This file is already attached.')
    claimed = Attachment.objects.filter(pk=attachment.pk, uploaded=False).delete()[0]
    if claimed:
        _delete_object(attachment.user_id, attachment.pk)
    elif Attachment.objects.filter(pk=attachment.pk).exists():
        raise ValidationError('This file is already attached.')


def abandon_uploads(*, older_than: timedelta = ABANDONED_AFTER) -> int:
    """Remove uploads started but never completed within `older_than`: their rows, then their
    objects (if any landed). The row is claimed first, with a conditional delete on
    `uploaded=False`, and the object deleted only after winning, so a `complete` arriving late
    finds no row instead of an attachment counted and billed with no object behind it. One bad row
    is logged and skipped. Returns how many were removed."""
    cutoff = timezone.now() - older_than
    removed = 0
    for attachment in Attachment.objects.filter(uploaded=False, created_at__lte=cutoff):
        try:
            claimed = Attachment.objects.filter(pk=attachment.pk, uploaded=False).delete()[0]
            if claimed:
                removed += claimed
                get_storage().delete_object(attachment_key(attachment.user_id, attachment.pk))
        except Exception:
            logger.exception('Failed to clean up abandoned upload %s', attachment.pk)
    return removed


def with_attachment_counts(queryset: QuerySet) -> QuerySet:
    """Annotate `attachment_count` (uploaded attachments; a pending upload isn't one yet) on a
    queryset of tasks. A subquery, not a join, like `comments.with_comment_counts`: a list already
    counts subtasks with a join, and two joins would multiply the counts."""
    counts = (
        Attachment.objects.filter(task=OuterRef('pk'), uploaded=True)
        .order_by()
        .values('task')
        .annotate(total=Count('pk'))
        .values('total')
    )
    return queryset.annotate(attachment_count=Coalesce(Subquery(counts), 0))


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
    # A pending upload's browser PUT may have landed between the delete above and the row going.
    # Keys are per attachment, not per task, so delete the pending rows' keys again now that the
    # rows are gone (best effort: the rows are already deleted, and failing here would only undo
    # the task's deletion). What still gets through is cleaned up by `complete_upload` when the
    # browser reports the finished PUT (`discard_orphan_object`); see the module docstring.
    pending_keys = [attachment_key(row.user_id, row.pk) for row in locked if not row.uploaded]
    if pending_keys:
        try:
            get_storage().delete_objects(pending_keys)
        except STORAGE_ERRORS:
            logger.exception(
                'Could not re-delete the objects of %d pending uploads', len(pending_keys)
            )
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
