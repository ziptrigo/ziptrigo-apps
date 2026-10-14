"""Deleting what Receipts stores: one receipt forever (`purge_receipt`), and everything a user has
(`delete_user_storage`, queued when the user is deleted)."""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.tasks import task
from django.utils import timezone

from ..models import InboundEmail, Receipt, ReceiptStatus
from .accounts import add_stored_bytes
from .storage import get_storage, user_prefix
from .vendors import drop_if_orphan

logger = logging.getLogger(__name__)


def purge_receipt(receipt: Receipt, *, trashed_before: datetime | None = None) -> bool:
    """Delete `receipt` for good: its file and preview from S3, its row, and the bytes from the
    account's storage count. If it was the last receipt of its inbound email, the stripped `.eml`
    goes too (the email row itself stays as the user's record of what arrived). If it was the last
    receipt of an unvetted vendor, the vendor goes too (a vetted one is the user's and stays).

    `receipt` may be a stale instance (the `purge_trash` job loads a batch, then purges one at a
    time), so everything is decided from the row as it is *now*, under its lock, before anything is
    deleted: it must still be in the Trash (and not being processed) and, when `trashed_before` is given (the job's
    retention cutoff), have been trashed at or before it. A receipt restored in between is left
    alone -- and a restore that comes after takes the lock only once the purge has finished, so it
    finds the receipt gone rather than half-deleted. Returns whether it was purged.

    The S3 deletes happen under that lock, with the `.eml` delete and the database changes, in one
    transaction (holding the receipt and inbound-email row locks): a failure anywhere leaves
    everything in place for the next run (the `purge_trash` job, or the user clicking "delete
    forever" again) to finish. The deletes are idempotent.

    Known gap: S3 is not transactional. If the file delete succeeds and the preview or `.eml`
    delete then fails, the database rolls back to "trashed" while the file is already gone, and a
    restore at that point brings back a receipt with no object (previews and downloads fail until
    the next purge attempt removes it). The old order had the same gap; the next run finishes it.
    """
    storage = get_storage()
    with transaction.atomic():
        # Lock the receipt, then its inbound email (the order a receipt settling in `processing`
        # uses too), so two siblings purged at once decide about the `.eml` one after the other:
        # the second sees the first gone and deletes it. Unlocked, each saw the other and neither
        # did, leaving an object that is billed forever.
        trashed = (
            Receipt.objects.select_for_update(of=('self',))
            .filter(pk=receipt.pk, deleted_at__isnull=False)
            .exclude(status=ReceiptStatus.PROCESSING)  # a run in flight would write for a ghost
        )
        if trashed_before is not None:
            trashed = trashed.filter(deleted_at__lte=trashed_before)
        current = trashed.first()
        if current is None:
            return False
        email = (
            InboundEmail.objects.select_for_update().filter(pk=current.inbound_email_id).first()
            if current.inbound_email_id is not None
            else None
        )
        for key in (current.file_key, current.preview_key):
            if key:
                storage.delete_object(key)
        delete_email_object = (
            email is not None
            and bool(email.raw_key)
            and not Receipt.objects.filter(inbound_email=email).exclude(pk=current.pk).exists()
        )
        freed = current.size_bytes + current.preview_size_bytes
        if email is not None and delete_email_object:
            storage.delete_object(email.raw_key)
            freed += email.size_bytes
            InboundEmail.objects.filter(pk=email.pk).update(raw_key='', size_bytes=0)
        # Receipts whose "possible duplicate of" pointed here are un-flagged by the FK's SET_NULL.
        deleted, _ = Receipt.objects.filter(pk=current.pk, deleted_at__isnull=False).delete()
        if deleted:
            add_stored_bytes(current.user_id, -freed)
            if current.vendor_ref_id is not None:
                drop_if_orphan(current.vendor_ref_id)
    return True


#: How long to wait before each retry of `delete_user_storage` (so it runs at most one more time
#: than there are delays). The `User` row is gone by then, so nothing else records that the objects
#: are still there: the task itself has to keep trying.
USER_STORAGE_RETRY_DELAYS = (
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)


@task(queue_name='receipts')
def delete_user_storage(user_id: str, attempt: int = 1) -> None:
    """Delete every object stored for a (deleted) user, in one `delete_prefix`. Queued by the
    `pre_delete(User)` hook in `apps.receipts.apps`.

    `delete_prefix` raises when S3 refuses any object, and the task queue never retries a failed
    task, so a failure queues this task again (`attempt` + 1) after the next of
    `USER_STORAGE_RETRY_DELAYS`: bounded, and durable because the retry is a queued task. Only
    after the last try does it give up, logging an error with the prefix -- the objects then stay
    in the bucket (and need removing by hand)."""
    prefix = user_prefix(UUID(user_id))
    try:
        deleted = get_storage().delete_prefix(prefix)
    except Exception:
        if attempt > len(USER_STORAGE_RETRY_DELAYS):
            logger.exception(
                'Giving up deleting the receipts objects under %s after %s attempts: '
                'they remain in the bucket',
                prefix,
                attempt,
            )
            return
        delay = USER_STORAGE_RETRY_DELAYS[attempt - 1]
        logger.warning(
            'Deleting the receipts objects under %s failed (attempt %s); retrying in %s',
            prefix,
            attempt,
            delay,
            exc_info=True,
        )
        _requeue_user_storage(user_id, attempt + 1, delay)
        return
    logger.info('Deleted %s receipts objects under %s', deleted, prefix)


def _requeue_user_storage(user_id: str, attempt: int, delay: timedelta) -> None:
    """Queue `delete_user_storage` again to run after `delay`. A backend that cannot defer a task
    (the immediate one the tests run on) runs it at once."""
    retry = delete_user_storage
    if retry.get_backend().supports_defer:
        retry = retry.using(run_after=timezone.now() + delay)
    retry.enqueue(user_id, attempt)
