"""Uploading to a file request (issue #306): the same direct-to-S3 multipart flow as a send's
(`services.uploads`: add a file, presign part URLs, resume, complete), against a request that is
open, by an uploader the access check let through (`services.request_access`).

Every new file is checked **under the request's row lock** against the per-file limit and, over
*all* of the request's files (every uploader's, finished or in progress), its `max_files` and
`max_total_size_bytes` -- the messages name the request. An upload is refused once the request is
done or unavailable (expired, disabled, suspended, deleted, taken down, on hold):
`NOT_ACCEPTING_MESSAGE`. The block list is checked at upload (the uploader's IP, and the account
email when signed in).

**S3 round trips stay out of the request's row lock** where they can: completing an upload
(`CompleteMultipartUpload` + `HeadObject`) and the S3 side of a removal run outside it; the lock is
taken only to re-check that the request is still open and that the file row is still there, and to
write the database (`complete_file`, `_remove`). A file that finishes in S3 while the request is
closed meanwhile has its object deleted again.

`Transfer.size_bytes` is kept equal to the sum of the uploaded files, so the owner is billed for
what is stored. A finished upload by anyone but the owner also pushes back the debounced "new
files" notice (`services.request_notices`).
"""

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DateTimeField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import FileTransferSettings, PreviewStatus, Transfer, TransferFile
from . import limits, renditions, uploads
from .actions import _require_actionable, _require_not_held
from .blocklist import check_not_blocked
from .downloads import is_available
from .request_uploader import Uploader, own_files
from .requests import NOT_ACCEPTING_MESSAGE, invalidate_zip, owner_can_be_told, refresh_size
from .storage import PART_SIZE_BYTES, S3Storage, get_storage, storage_key

logger = logging.getLogger(__name__)

#: How long after the last completed upload the owner's "new files" notice is sent: every finished
#: upload resets it, so one batch of files is one notice.
NOTICE_DEBOUNCE = timedelta(minutes=15)


def require_accepting(transfer: Transfer) -> None:
    """Raise `ValidationError(NOT_ACCEPTING_MESSAGE)` unless `transfer` is an open request whose
    link works right now and whose owner's account can still sign in (a deactivated owner's
    request neither takes uploads nor keeps being billed for them)."""
    if not transfer.is_open or not is_available(transfer):
        raise ValidationError(NOT_ACCEPTING_MESSAGE)
    if not owner_can_be_told(transfer.owner):
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
    if not file.upload_id and not file.uploaded:
        # A completion that found the wrong size deleted the object and cleared the upload id:
        # start the file over rather than leave it with no way to continue.
        return uploads.restart_upload(file, storage=storage), [], True
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
    """Complete an upload: S3 assembles the parts and the size is verified **outside any lock**;
    then, under the request's row lock, the request must still be open and the file row still
    there, and the row is flipped to uploaded, `size_bytes` is kept equal to what is uploaded, a
    stale zip is forgotten and -- unless the owner uploaded it -- the debounced notice starts or is
    pushed back.

    The lock is why finishing and marking the request done take turns: a file that finishes first
    is kept, one that finishes after is refused. If the request was closed (done, disabled,
    expired...) or the file removed while S3 was working, the object S3 just assembled is deleted
    again and the upload is refused.
    """
    require_accepting(transfer)
    check_not_blocked(email=uploader.email or None, ip=ip)
    storage = storage or get_storage()

    fresh = TransferFile.objects.filter(pk=file.pk, transfer=transfer, uploaded=False).first()
    if fresh is None:
        raise ValidationError('This file is no longer being uploaded.')
    upload_id = fresh.upload_id
    info = uploads.finish_upload_in_storage(fresh, parts, storage=storage)

    gone = False
    refused: ValidationError | None = None
    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        try:
            require_accepting(locked)
        except ValidationError as exc:
            refused = exc
        if refused is None:
            now = timezone.now()
            done = TransferFile.objects.filter(
                pk=fresh.pk, transfer=locked, uploaded=False, upload_id=upload_id
            ).update(
                checksum=info.checksum_sha256,
                uploaded=True,
                uploaded_at=now,
                upload_id='',
                preview_type=fresh.preview_type,
            )
            if not done:
                gone = True
            else:
                fresh.checksum = info.checksum_sha256
                fresh.uploaded = True
                fresh.uploaded_at = now
                fresh.upload_id = ''
                refresh_size(locked)
                invalidate_zip(locked, storage=storage)
                is_owner = uploader.user is not None and uploader.user.pk == locked.owner_id
                if not is_owner:
                    Transfer.objects.filter(pk=locked.pk).update(
                        upload_notice_due_at=now + NOTICE_DEBOUNCE,
                        upload_notice_since=Coalesce(
                            'upload_notice_since', Value(now, DateTimeField())
                        ),
                    )
        else:
            # Closed meanwhile: the unfinished row would never be uploaded; drop it with the object.
            TransferFile.objects.filter(pk=fresh.pk, transfer=locked, uploaded=False).delete()

    if refused is not None or gone:
        _delete_object(fresh.storage_key, storage)
        if refused is not None:
            raise refused
        raise ValidationError('This file is no longer being uploaded.')
    return fresh


def remove_own_file(
    transfer: Transfer, file: TransferFile, *, storage: S3Storage | None = None
) -> None:
    """An uploader removes one of their own files (only while the request is open -- checked
    again under the lock): the S3 object (or unfinished upload) and the row are deleted and
    `size_bytes` drops. No notice is sent."""
    require_accepting(transfer)
    _remove(transfer, file, storage, uploader=True)


def remove_file_as_owner(
    transfer: Transfer, file: TransferFile, *, storage: S3Storage | None = None
) -> None:
    """The owner removes any file of their request, open or done (dashboard, API)."""
    _require_actionable(transfer)
    _require_not_held(transfer)
    _remove(transfer, file, storage, uploader=False)


def _delete_object(key: str, storage: S3Storage) -> None:
    try:
        storage.delete_object(key)
    except Exception:
        logger.exception('Could not delete the object %s', key)


def _remove(
    transfer: Transfer, file: TransferFile, storage: S3Storage | None, *, uploader: bool
) -> None:
    """Delete the file's row under the request's lock (re-reading it there, and re-checking that
    the request is still open for an uploader), then its S3 side after the commit: the unfinished
    multipart upload is aborted or the object deleted, best effort and logged. A failure leaves an
    unbilled orphan that the request's prefix sweep removes when it ends (and the bucket's
    lifecycle rule aborts unfinished uploads); the row is already gone, so the limits are freed."""
    storage = storage or get_storage()
    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if uploader:
            require_accepting(locked)
        fresh = TransferFile.objects.filter(pk=file.pk, transfer=locked).first()
        if fresh is None:
            return
        key, upload_id, uploaded = fresh.storage_key, fresh.upload_id, fresh.uploaded
        previews = (
            renditions.preview_prefix(locked.pk, fresh.pk)
            if fresh.preview_status in (PreviewStatus.READY, PreviewStatus.BUILDING)
            else ''
        )
        fresh.delete()
        refresh_size(locked)
        invalidate_zip(locked, storage=storage)
    transfer.size_bytes = locked.size_bytes
    if upload_id:
        try:
            storage.abort_multipart_upload(key, upload_id)
        except Exception:
            logger.exception('Could not abort the unfinished upload of %s', key)
    if uploaded:
        _delete_object(key, storage)
    if previews:
        # The downsized preview pages (a build still running deletes its own output when it finds
        # the row gone, see `renditions.run_build`).
        try:
            storage.delete_prefix(previews)
        except Exception:
            logger.exception('Could not delete the preview pages under %s', previews)
