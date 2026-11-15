"""Uploading to a file request (issue #306): the same direct-to-S3 multipart flow as a send's
(`services.uploads`: add a file, presign part URLs, resume, complete), against a request that is
open, by an uploader the access check let through (`services.request_access`).

Every new file is checked **under the request's row lock** against the per-file limit and, over
*all* of the request's files (every uploader's, finished or in progress), its `max_files` and
`max_total_size_bytes` -- the messages name the request. An upload is refused once the request is
done or unavailable (expired, disabled, suspended, deleted, taken down, on hold):
`NOT_ACCEPTING_MESSAGE`. The block list is checked at upload (the uploader's IP, and the account
email when signed in).

`Transfer.size_bytes` is kept equal to the sum of the uploaded files, so the owner is billed for
what is stored. A finished upload by anyone but the owner also pushes back the debounced "new
files" notice (`services.request_notices`).
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DateTimeField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import FileTransferSettings, Transfer, TransferFile
from . import limits, uploads
from .actions import _require_actionable, _require_not_held
from .blocklist import check_not_blocked
from .downloads import is_available
from .request_uploader import Uploader, own_files
from .requests import NOT_ACCEPTING_MESSAGE, invalidate_zip, refresh_size
from .storage import PART_SIZE_BYTES, S3Storage, get_storage, storage_key

#: How long after the last completed upload the owner's "new files" notice is sent: every finished
#: upload resets it, so one batch of files is one notice.
NOTICE_DEBOUNCE = timedelta(minutes=15)


def require_accepting(transfer: Transfer) -> None:
    """Raise `ValidationError(NOT_ACCEPTING_MESSAGE)` unless `transfer` is an open request whose
    link works right now."""
    if not transfer.is_open or not is_available(transfer):
        raise ValidationError(NOT_ACCEPTING_MESSAGE)


def _validate_new_file(transfer: Transfer, size: int, settings_row: FileTransferSettings) -> None:
    if size <= 0:
        raise ValidationError('File is empty.')
    per_file = settings_row.logged_in_max_file_size_bytes
    if size > per_file:
        raise ValidationError(
            f'File is larger than the {limits.size_text(per_file)} limit per file.'
        )

    max_files = transfer.max_files
    if max_files is None:
        max_files = settings_row.logged_in_max_files
    if transfer.files.count() >= max_files:
        raise ValidationError(
            f'This request accepts at most {limits.count_text(max_files, "file")}.'
        )

    max_total = transfer.max_total_size_bytes
    if max_total is None:
        max_total = settings_row.logged_in_max_total_size_bytes
    used = transfer.files.aggregate(total=Sum('size'))['total'] or 0
    if used + size > max_total:
        raise ValidationError(
            f'This request accepts at most {limits.size_text(max_total)} in total.'
        )


def add_file(
    transfer: Transfer,
    uploader: Uploader,
    name: str,
    size: int,
    *,
    ip: str | None = None,
    client_last_modified: int | None = None,
    storage: S3Storage | None = None,
) -> TransferFile:
    """Register a new file on an open request and start its multipart upload; the caller still
    needs `presign_parts` for the part URLs.

    Raises:
        ValidationError: the request is not accepting files, the file is empty or over a limit.
        blocklist.BlockedSenderError: the uploader's IP or email is on the block list.
    """
    require_accepting(transfer)
    check_not_blocked(email=uploader.email or None, ip=ip)
    name = limits.validate_filename(name)
    settings_row = FileTransferSettings.load()
    storage = storage or get_storage()

    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        require_accepting(locked)
        _validate_new_file(locked, size, settings_row)

        file = TransferFile(
            transfer=locked,
            name=name,
            size=size,
            client_last_modified=client_last_modified,
            part_size_bytes=PART_SIZE_BYTES,
            uploader_user=uploader.user,
            uploader_name=uploader.name,
            uploader_email=uploader.email,
            uploader_token_hash='' if uploader.user is not None else uploader.token_hash,
            uploader_ip=ip,
        )
        key = storage_key(locked.id, file.id)
        file.storage_key = key
        file.upload_id = storage.create_multipart_upload(key)
        file.save()
    return file


def own_file(transfer: Transfer, uploader: Uploader, file_id: object) -> TransferFile | None:
    """One of the uploader's own files of this request, or `None` (someone else's file, an unknown
    id or a malformed one: the caller answers a 404 for all of them)."""
    try:
        return own_files(transfer, uploader).filter(pk=file_id).first()
    except ValueError, ValidationError:
        return None


def presign_parts(
    transfer: Transfer, file: TransferFile, parts: list[dict], *, storage: S3Storage | None = None
) -> dict[int, str]:
    """Presigned PUT URLs for part of the file's upload; refused once the request is done."""
    require_accepting(transfer)
    return uploads.presign_parts(file, parts, storage=storage)


def resume_file(
    transfer: Transfer, file: TransferFile, *, storage: S3Storage | None = None
) -> tuple[TransferFile, list[dict], bool]:
    """What S3 already has of an interrupted upload: `(file, parts, restarted)`, restarting the
    upload transparently when S3 no longer knows it. Refused once the request is done."""
    require_accepting(transfer)
    try:
        return file, uploads.list_uploaded_parts(file, storage=storage), False
    except uploads.UploadExpired:
        return uploads.restart_upload(file, storage=storage), [], True


def complete_file(
    transfer: Transfer,
    file: TransferFile,
    parts: list[dict],
    uploader: Uploader,
    *,
    ip: str | None = None,
    storage: S3Storage | None = None,
) -> TransferFile:
    """Complete an upload (S3 assembles the parts and the size is verified), then, in the same
    transaction under the request's lock: keep `size_bytes` equal to what is uploaded, forget a
    stale zip and -- unless the owner uploaded it -- start or push back the debounced notice.

    The lock is why finishing and marking the request done take turns: a file that finishes first
    is kept, one that finishes after is refused (and `mark_done` has already deleted its row).
    """
    require_accepting(transfer)
    check_not_blocked(email=uploader.email or None, ip=ip)
    storage = storage or get_storage()

    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        require_accepting(locked)
        fresh = TransferFile.objects.filter(pk=file.pk, transfer=locked, uploaded=False).first()
        if fresh is None:
            raise ValidationError('This file is no longer being uploaded.')
        uploads.complete_file_upload(fresh, parts, storage=storage)
        refresh_size(locked)
        invalidate_zip(locked, storage=storage)
        is_owner = uploader.user is not None and uploader.user.pk == locked.owner_id
        if not is_owner:
            uploaded_at = fresh.uploaded_at or timezone.now()
            Transfer.objects.filter(pk=locked.pk).update(
                upload_notice_due_at=uploaded_at + NOTICE_DEBOUNCE,
                upload_notice_since=Coalesce(
                    'upload_notice_since', Value(uploaded_at, DateTimeField())
                ),
            )
    return fresh


def remove_own_file(
    transfer: Transfer, file: TransferFile, *, storage: S3Storage | None = None
) -> None:
    """An uploader removes one of their own files (only while the request is open): the S3 object
    (or unfinished upload) and the row are deleted and `size_bytes` drops. No notice is sent."""
    require_accepting(transfer)
    _remove(transfer, file, storage)


def remove_file_as_owner(
    transfer: Transfer, file: TransferFile, *, storage: S3Storage | None = None
) -> None:
    """The owner removes any file of their request, open or done (dashboard, API)."""
    _require_actionable(transfer)
    _require_not_held(transfer)
    _remove(transfer, file, storage)


def _remove(transfer: Transfer, file: TransferFile, storage: S3Storage | None) -> None:
    storage = storage or get_storage()
    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if not TransferFile.objects.filter(pk=file.pk, transfer=locked).exists():
            return
        uploads.remove_file(file, storage=storage)
        refresh_size(locked)
        invalidate_zip(locked, storage=storage)
    transfer.size_bytes = locked.size_bytes
