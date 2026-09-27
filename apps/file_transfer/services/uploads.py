"""Draft transfer and upload management: creating a draft, adding/removing files, issuing
presigned multipart-upload URLs, and completing a file's upload once the browser has PUT every
part directly to S3 (spec section 2). Shared by the send-page HTMX/JSON views and, for cleanup,
by the `cleanup_drafts` job.
"""

from botocore.exceptions import ClientError
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import FileTransferSettings, Transfer, TransferFile, TransferStatus
from . import limits
from .storage import S3Storage, get_storage, storage_key


def create_draft(owner: AbstractBaseUser) -> Transfer:
    """Start a new transfer: a draft with no files yet."""
    return Transfer.objects.create(owner=owner, status=TransferStatus.DRAFT)


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

    limits.validate_new_file(transfer, size, FileTransferSettings.load())

    storage = storage or get_storage()
    file = TransferFile(transfer=transfer, name=name, size=size)
    key = storage_key(transfer.id, file.id, name)
    file.storage_key = key
    file.upload_id = storage.create_multipart_upload(key)
    file.save()
    return file


def presign_parts(
    file: TransferFile, part_numbers: list[int], *, storage: S3Storage | None = None
) -> dict[int, str]:
    """Presigned PUT URLs for the given 1-based part numbers of `file`'s multipart upload."""
    if not file.upload_id:
        raise ValidationError('File has no upload in progress.')
    storage = storage or get_storage()
    return {
        part_number: storage.presign_part_url(file.storage_key, file.upload_id, part_number)
        for part_number in part_numbers
    }


def complete_file_upload(
    file: TransferFile, parts: list[dict], *, storage: S3Storage | None = None
) -> TransferFile:
    """Complete `file`'s multipart upload and verify it landed in S3 with the expected size.

    `parts` is `[{'PartNumber': n, 'ETag': etag}, ...]`, one entry per part the browser PUT,
    in order.

    Raises:
        ValidationError: S3 rejected completing the upload (e.g. no parts were ever PUT), or the
            object's size in S3 doesn't match what was declared up front.
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
    """Abandon an entire draft (or unconfirmed) transfer: abort/delete every file's S3 object,
    then soft-delete the transfer. Used by `cleanup_drafts`."""
    storage = storage or get_storage()
    for file in transfer.files.all():
        if file.upload_id:
            storage.abort_multipart_upload(file.storage_key, file.upload_id)
        if file.uploaded:
            storage.delete_object(file.storage_key)
    transfer.files.all().delete()
    transfer.status = TransferStatus.DELETED
    transfer.deleted_at = timezone.now()
    transfer.save(update_fields=['status', 'deleted_at'])
