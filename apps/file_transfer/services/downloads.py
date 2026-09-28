"""Public download page logic (spec sections 3-4): availability, password gating, and per-file
download counting.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import DownloadEvent, Transfer, TransferFile, TransferStatus
from .emails import send_download_notification
from .lifecycle import end_transfer
from .password import verify_password
from .storage import S3Storage, get_storage


def download_count(transfer: Transfer) -> int:
    return transfer.download_count


def downloads_remaining(transfer: Transfer) -> int | None:
    return transfer.downloads_remaining


def is_available(transfer: Transfer) -> bool:
    """Whether the transfer's public page should work at all. The page itself must never say
    *why* it doesn't (spec section 3): expired, disabled, suspended, deleted and limit-reached
    all render the same neutral message."""
    if transfer.status != TransferStatus.ACTIVE:
        return False
    if transfer.expires_at and transfer.expires_at <= timezone.now():
        return False
    remaining = downloads_remaining(transfer)
    if remaining is not None and remaining <= 0:
        return False
    return True


def requires_password(transfer: Transfer) -> bool:
    return bool(transfer.password_hash)


def check_password(transfer: Transfer, raw_password: str) -> bool:
    if not transfer.password_hash:
        return True
    return verify_password(raw_password, transfer.password_hash)


def record_download(
    transfer: Transfer,
    file: TransferFile,
    ip: str | None,
    *,
    storage: S3Storage | None = None,
) -> str:
    """Record a download of `file` and return a short-lived presigned URL for it.

    Locks the transfer row for the duration of the check-count-record sequence, so N concurrent
    requests against a transfer with one download remaining can't all slip through before any of
    them is counted. The presigned URL itself is only built after the transaction commits.

    Raises:
        ValidationError: the transfer isn't available, or `file` doesn't belong to it.
    """
    with transaction.atomic():
        locked_transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if not is_available(locked_transfer):
            raise ValidationError('This transfer is no longer available.')
        if file.transfer_id != locked_transfer.id:
            raise ValidationError('File does not belong to this transfer.')

        DownloadEvent.objects.create(transfer=locked_transfer, file=file, ip=ip)
        transaction.on_commit(
            lambda: send_download_notification.enqueue(str(locked_transfer.id), str(file.id))
        )

        # End the transfer the moment the limit is *reached*, rather than waiting for the next
        # `expire_transfers` tick, so the same link can't be reused past its limit in the
        # meantime. Deletion of the files themselves is deferred (`delete_files=False`): the
        # presigned URL this call is about to hand back must still resolve, so
        # `expire_transfers` removes the objects once it's had time to expire instead. That job
        # also re-checks this ending as a fallback in case the process dies right here.
        remaining = downloads_remaining(locked_transfer)
        if remaining is not None and remaining <= 0:
            end_transfer(locked_transfer, TransferStatus.EXPIRED, delete_files=False)

    storage = storage or get_storage()
    return storage.presigned_get_url(file.storage_key, file.name)
