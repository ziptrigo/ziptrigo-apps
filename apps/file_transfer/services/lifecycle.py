"""Ending a transfer, however it ends: natural expiry, max-downloads reached, sender disable,
dashboard delete-now, or an out-of-credits grace period running out (spec sections 3, 7 and 12).
"""

from collections.abc import Callable, Collection

from django.db import transaction
from django.db.models import DateTimeField, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import ENDED_STATUSES, Transfer, TransferStatus, ZipStatus
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


#: The statuses a transfer can still be ended from: every one that isn't already terminal.
_LIVE_STATUSES = tuple(s for s in TransferStatus if s not in ENDED_STATUSES)


def end_transfer(
    transfer: Transfer,
    status: str,
    *,
    delete_files: bool = True,
    notify: bool = False,
    storage: S3Storage | None = None,
    before_delete: Callable[[], None] | None = None,
    from_statuses: Collection[str] = _LIVE_STATUSES,
    only_if: Q | None = None,
) -> bool:
    """Move `transfer` to a terminal availability status, optionally deleting its files and
    notifying the owner. Returns whether this call ended it.

    The move is a conditional write (`status IN from_statuses`), so `transfer` may be a stale
    instance: if the row has since left those statuses (another path ended it, a top-up
    re-enabled it, the sender's `finalize_send` sent it) nothing changes -- no status write, no
    `before_delete`, no S3 delete, no email -- and this returns `False`. Only the caller that won
    the claim deletes the files and notifies. A caller that knows more passes a narrower
    `from_statuses` (the grace-period job: `SUSPENDED` only), so it can't end a transfer that has
    since moved to another live status. The default is every non-terminal status.

    Status is not the only precondition a caller decided on from a stale read: a hold applied
    after delete-now's check, an expiry the owner extended, a `max_downloads` they raised, a
    transfer re-enabled and suspended again (a fresh grace period) all leave the status in
    `from_statuses`. `only_if` is an extra `Q` ANDed into the claim's `WHERE`, so a caller passes
    the very condition it acted on (`expires_at__lte=now`, `held_for_review_at__isnull=True`, ...)
    and loses the claim -- `False`, nothing done -- when the row no longer satisfies it.

    `status` is `TransferStatus.EXPIRED` (natural expiry, or max downloads reached),
    `TransferStatus.DELETED` (dashboard delete-now, or an out-of-credits grace period running
    out), or `TransferStatus.TAKEN_DOWN` (admin takedown, `services.takedown.take_down_transfer`).
    A second call on an ended transfer is therefore a no-op (a failed S3 delete is retried by
    `jobs.expire_transfers`' deferred-deletion sweep, not by calling this again).

    `before_delete`, if given, runs inside the *same* atomic transaction as the status change,
    before the S3 delete is even attempted (issue #59 code review: a takedown must mark its
    pending reports actioned in that same transaction, not after a S3 call that might raise --
    `take_down_transfer` is the one caller that uses this). Anything a caller needs committed
    atomically alongside the status flip, and *not* rolled back by a storage failure, belongs here
    rather than after this call returns.

    `delete_files=False` defers the actual S3 deletion: the transfer is immediately marked
    unavailable (`is_available()` already checks `status`), but its objects are left alone so a
    presigned download URL just handed out for it (the download that reached `max_downloads`,
    say) still resolves. `ended_at` records when that grace window starts;
    `apps.file_transfer.jobs.expire_transfers` deletes the objects (and fires `notify`, if it was
    requested) once `GET_URL_EXPIRES_SECONDS` has safely passed.
    """
    now = timezone.now()
    changes = {'status': status, 'ended_at': Coalesce('ended_at', Value(now, DateTimeField()))}
    if status == TransferStatus.DELETED:
        changes['deleted_at'] = Coalesce('deleted_at', Value(now, DateTimeField()))
    with transaction.atomic():
        claim = Transfer.objects.filter(pk=transfer.pk, status__in=from_statuses)
        if only_if is not None:
            claim = claim.filter(only_if)
        claimed = claim.update(**changes)
        if not claimed:
            return False
        if before_delete is not None:
            before_delete()
    transfer.refresh_from_db(fields=['status', 'ended_at', 'deleted_at'])

    if delete_files:
        delete_transfer_files(transfer, storage=storage)
        if notify:
            _notify_files_deleted(transfer)
    return True


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
