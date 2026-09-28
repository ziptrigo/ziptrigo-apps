"""Draft transfer and upload management: creating a draft, adding/removing files, issuing
presigned multipart-upload URLs, and completing a file's upload once the browser has PUT every
part directly to S3 (spec section 2). Shared by the send-page HTMX/JSON views and, for cleanup,
by the `cleanup_drafts` job.
"""

import math

from botocore.exceptions import ClientError
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.utils import timezone

from ..models import FileTransferSettings, Transfer, TransferFile, TransferStatus
from . import limits
from .storage import PART_SIZE_BYTES, S3Storage, get_storage, storage_key, transfer_prefix


def create_draft(owner: AbstractBaseUser) -> Transfer:
    """Start a new transfer: a draft with no files yet."""
    return Transfer.objects.create(owner=owner, status=TransferStatus.DRAFT)


def get_or_create_draft(owner: AbstractBaseUser) -> Transfer:
    """Reuse the owner's most recent still-empty draft rather than creating a new one on every
    visit to the send page: `send_page` used to call `create_draft` unconditionally, so simply
    reloading it (or leaving the tab open) left an "Untitled transfer" row on the dashboard for up
    to the 24h `cleanup_drafts` grace period. A draft that already has files is left alone and a
    fresh one is created instead -- the send page's file list is only tracked in the browser, so
    reusing one that already has files would silently attach new uploads to files the page has no
    record of.
    """
    existing = (
        Transfer.objects.filter(owner=owner, status=TransferStatus.DRAFT)
        .annotate(_file_count=Count('files'))
        .filter(_file_count=0)
        .order_by('-created_at')
        .first()
    )
    return existing or create_draft(owner)


def add_file(
    transfer: Transfer,
    name: str,
    size: int,
    *,
    storage: S3Storage | None = None,
) -> TransferFile:
    """Register a new file on a draft transfer and start its multipart upload.

    Returns the `TransferFile`; the caller still needs `presign_parts` to get upload URLs.

    Raises:
        ValidationError: the transfer isn't a draft, or the file would break a logged-in limit
            (spec section 1: max file size, max files, max total size).
    """
    if transfer.status != TransferStatus.DRAFT:
        raise ValidationError('Files can only be added to a draft transfer.')

    name = limits.validate_filename(name)
    limits.validate_new_file(transfer, size, FileTransferSettings.load())

    storage = storage or get_storage()
    file = TransferFile(transfer=transfer, name=name, size=size)
    # Keyed by file id only (not the filename): see `storage_key`'s docstring for why.
    key = storage_key(transfer.id, file.id)
    file.storage_key = key
    file.upload_id = storage.create_multipart_upload(key)
    file.save()
    return file


def _max_part_number(file_size: int) -> int:
    return max(1, math.ceil(file_size / PART_SIZE_BYTES))


def _expected_part_content_length(file_size: int, part_number: int, part_count: int) -> int:
    """Every part is `PART_SIZE_BYTES` except the last, which is whatever's left over."""
    if part_number < part_count:
        return PART_SIZE_BYTES
    return file_size - PART_SIZE_BYTES * (part_count - 1)


def presign_parts(
    file: TransferFile, parts: list[dict], *, storage: S3Storage | None = None
) -> dict[int, str]:
    """Presigned PUT URLs for the given parts of `file`'s multipart upload.

    `parts` is `[{'part_number': n, 'checksum_sha256': <base64 SHA-256 of that part>}, ...]`: the
    browser computes each part's checksum with `crypto.subtle` *before* asking for its URL, and
    that checksum is baked into the presigned URL's signature (`ChecksumSHA256`, spec section 11)
    -- S3 rejects the `UploadPart` call outright if the header on the PUT doesn't match the value
    the URL was signed for, which is what actually verifies the bytes made it over intact (a
    presigned URL can't itself carry a value the *server* doesn't already know, so this only works
    because the checksum is computed client-side first and handed to us, not the other way
    round). The content length expected for each part number is signed the same way, so a part
    can't be padded past its declared size either.

    Raises:
        ValidationError: the upload isn't in progress, a part number is outside the valid
            `1..ceil(size / PART_SIZE_BYTES)` range for this file's declared size (which would
            otherwise let a sender request far more parts -- and so upload far more bytes -- than
            the size it declared), or a checksum isn't a well-formed base64-encoded SHA-256.
    """
    if not file.upload_id:
        raise ValidationError('File has no upload in progress.')

    part_count = _max_part_number(file.size)
    storage = storage or get_storage()
    urls: dict[int, str] = {}
    for part in parts:
        part_number = part['part_number']
        if part_number < 1 or part_number > part_count:
            raise ValidationError(f'Invalid part number {part_number} for this file.')
        checksum = limits.validate_checksum_sha256(part['checksum_sha256'])
        content_length = _expected_part_content_length(file.size, part_number, part_count)
        urls[part_number] = storage.presign_part_url(
            file.storage_key,
            file.upload_id,
            part_number,
            content_length=content_length,
            checksum_sha256=checksum,
        )
    return urls


def complete_file_upload(
    file: TransferFile, parts: list[dict], *, storage: S3Storage | None = None
) -> TransferFile:
    """Complete `file`'s multipart upload and verify it landed in S3 with the expected size.

    `parts` is `[{'PartNumber': n, 'ETag': etag, 'ChecksumSHA256': checksum}, ...]`, one entry per
    part the browser PUT, in order; passed straight through to S3's `CompleteMultipartUpload`.

    Raises:
        ValidationError: S3 rejected completing the upload (e.g. no parts were ever PUT), or the
            object's size in S3 doesn't match what was declared up front -- in which case the
            object S3 just assembled is deleted immediately rather than left as an orphan (the
            completed upload is no longer "in progress", so neither `remove_file` nor
            `abort_draft` would otherwise ever clean it up).
    """
    if not file.upload_id:
        raise ValidationError('File has no upload in progress.')

    storage = storage or get_storage()
    try:
        storage.complete_multipart_upload(file.storage_key, file.upload_id, parts)
        info = storage.head_object(file.storage_key)
    except ClientError as exc:
        raise ValidationError(f'Could not complete the upload: {exc}') from exc

    if info.size != file.size:
        storage.delete_object(file.storage_key)
        file.upload_id = ''
        file.save(update_fields=['upload_id'])
        raise ValidationError(
            f'Uploaded file size ({info.size}) does not match the declared size ({file.size}).'
        )

    file.checksum = info.checksum_sha256
    file.uploaded = True
    file.upload_id = ''
    file.save(update_fields=['checksum', 'uploaded', 'upload_id'])
    return file


def remove_file(file: TransferFile, *, storage: S3Storage | None = None) -> None:
    """Remove a file from a draft transfer: abort its multipart upload (if still in progress),
    delete its object from S3 (if the upload had completed), then drop the row."""
    storage = storage or get_storage()
    if file.upload_id:
        storage.abort_multipart_upload(file.storage_key, file.upload_id)
    if file.uploaded:
        storage.delete_object(file.storage_key)
    file.delete()


def abort_draft(transfer: Transfer, *, storage: S3Storage | None = None) -> None:
    """Abandon an entire draft (or unconfirmed) transfer: abort every file's in-progress
    multipart upload, sweep every object under the transfer's S3 prefix (rather than trusting
    each file's `uploaded` flag, which is one more place a bug could leave an orphan), then
    soft-delete the transfer. Used by `cleanup_drafts`."""
    storage = storage or get_storage()
    for file in transfer.files.all():
        if file.upload_id:
            storage.abort_multipart_upload(file.storage_key, file.upload_id)
    storage.delete_prefix(transfer_prefix(transfer.id))
    transfer.files.all().delete()
    transfer.status = TransferStatus.DELETED
    transfer.deleted_at = timezone.now()
    transfer.save(update_fields=['status', 'deleted_at'])
