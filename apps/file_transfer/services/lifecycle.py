"""Ending a transfer, however it ends: natural expiry, max-downloads reached, sender disable,
dashboard delete-now, or an out-of-credits grace period running out (spec sections 3, 7 and 12).
"""

from django.db import transaction
from django.utils import timezone

from ..models import Transfer, TransferStatus, ZipStatus
from .emails import send_files_deleted_notification
from .storage import S3Storage, get_storage, transfer_prefix


def delete_transfer_files(transfer: Transfer, *, storage: S3Storage | None = None) -> None:
    """Delete every S3 object under `transfer`'s prefix right now and record that it happened.

    Idempotent: safe to call again on a transfer whose files are already gone (`delete_prefix`
    just finds nothing to delete).

    Also resets `zip_status`/`zip_key` back to `NONE` -- the "download all" zip lives under this
    same prefix and so is deleted along with everything else here; leaving `zip_status` at
    `READY`/`BUILDING` afterwards would let the download page keep claiming a zip is available (or
    still preparing) when there's nothing left to build it from at all (`services.zip.build_zip`
    separately guards the still-in-flight case: a build racing this deletion re-checks after it
    finishes and cleans up after itself either way).
    """
    storage = storage or get_storage()
    storage.delete_prefix(transfer_prefix(transfer.id))
    now = timezone.now()
    Transfer.objects.filter(pk=transfer.pk).update(
        files_deleted_at=now, zip_status=ZipStatus.NONE, zip_key=''
    )
    transfer.files_deleted_at = now
    transfer.zip_status = ZipStatus.NONE
    transfer.zip_key = ''


def _notify_files_deleted(transfer: Transfer) -> None:
    transfer_id = str(transfer.id)
    transaction.on_commit(lambda: send_files_deleted_notification.enqueue(transfer_id))


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

    `delete_files=False` defers the actual S3 deletion: the transfer is immediately marked
    unavailable (`is_available()` already checks `status`), but its objects are left alone so a
    presigned download URL just handed out for it (the download that reached `max_downloads`,
    say) still resolves. `ended_at` records when that grace window starts;
    `apps.file_transfer.jobs.expire_transfers` deletes the objects (and fires `notify`, if it was
    requested) once `GET_URL_EXPIRES_SECONDS` has safely passed.
    """
    now = timezone.now()
    with transaction.atomic():
        transfer.status = status
        if not transfer.ended_at:
            transfer.ended_at = now
        if status == TransferStatus.DELETED and not transfer.deleted_at:
            transfer.deleted_at = now
        transfer.save(update_fields=['status', 'ended_at', 'deleted_at'])

    if delete_files:
        delete_transfer_files(transfer, storage=storage)
        if notify:
            _notify_files_deleted(transfer)
    return transfer


def finish_deferred_deletion(
    transfer: Transfer, *, notify: bool, storage: S3Storage | None = None
) -> None:
    """Delete the files of a transfer previously ended with `delete_files=False`, once its grace
    window (spec: any presigned GET URL it may have handed out) has passed. Idempotent: a no-op
    if the files are already gone."""
    if transfer.files_deleted_at:
        return
    delete_transfer_files(transfer, storage=storage)
    if notify:
        _notify_files_deleted(transfer)
