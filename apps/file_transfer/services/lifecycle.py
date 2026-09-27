"""Ending a transfer, however it ends: natural expiry, max-downloads reached, sender disable,
dashboard delete-now, or an out-of-credits grace period running out (spec sections 3, 7 and 12).
"""

from django.db import transaction
from django.utils import timezone

from ..models import Transfer, TransferStatus
from .emails import send_files_deleted_notification
from .storage import S3Storage, get_storage, transfer_prefix


def delete_transfer_files(transfer: Transfer, *, storage: S3Storage | None = None) -> None:
    storage = storage or get_storage()
    storage.delete_prefix(transfer_prefix(transfer.id))


def end_transfer(
    transfer: Transfer,
    status: str,
    *,
    delete_files: bool = True,
    notify: bool = False,
    storage: S3Storage | None = None,
) -> Transfer:
    """Move `transfer` to a terminal availability status, optionally deleting its files and
    notifying the owner.

    `status` is `TransferStatus.EXPIRED` (natural expiry, or max downloads reached) or
    `TransferStatus.DELETED` (dashboard delete-now, or an out-of-credits grace period running
    out). Idempotent other than harmlessly re-issuing the S3 delete and, if `notify`, the email.
    """
    if delete_files:
        delete_transfer_files(transfer, storage=storage)

    with transaction.atomic():
        transfer.status = status
        if status == TransferStatus.DELETED and not transfer.deleted_at:
            transfer.deleted_at = timezone.now()
        transfer.save(update_fields=['status', 'deleted_at'])
        if notify:
            transfer_id = str(transfer.id)
            transaction.on_commit(lambda: send_files_deleted_notification.enqueue(transfer_id))
    return transfer
