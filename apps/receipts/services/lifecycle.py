"""Deleting what Receipts stores: one receipt forever (`purge_receipt`), and everything a user has
(`delete_user_storage`, queued when the user is deleted)."""

import logging
from uuid import UUID

from django.db import transaction
from django.tasks import task

from ..models import InboundEmail, Receipt
from .accounts import add_stored_bytes
from .storage import get_storage, user_prefix

logger = logging.getLogger(__name__)


def purge_receipt(receipt: Receipt) -> None:
    """Delete `receipt` for good: its file and preview from S3, its row, and the bytes from the
    account's storage count. If it was the last receipt of its inbound email, the stripped `.eml`
    goes too (the email row itself stays as the user's record of what arrived).

    The receipt's own S3 deletes come first and are idempotent; the `.eml` delete and the
    database changes happen in one transaction (holding the receipt and inbound-email row locks), so
    a failure anywhere leaves everything in place for the next run (the `purge_trash` job, or the
    user clicking "delete forever" again) to finish.
    """
    storage = get_storage()
    for key in (receipt.file_key, receipt.preview_key):
        if key:
            storage.delete_object(key)

    with transaction.atomic():
        # Lock the receipt, then its inbound email (the order a receipt settling in `processing`
        # uses too), so two siblings purged at once decide about the `.eml` one after the other:
        # the second sees the first gone and deletes it. Unlocked, each saw the other and neither
        # did, leaving an object that is billed forever.
        list(Receipt.objects.select_for_update(of=('self',)).filter(pk=receipt.pk).values('pk'))
        email = (
            InboundEmail.objects.select_for_update().filter(pk=receipt.inbound_email_id).first()
            if receipt.inbound_email_id is not None
            else None
        )
        delete_email_object = (
            email is not None
            and bool(email.raw_key)
            and not Receipt.objects.filter(inbound_email=email).exclude(pk=receipt.pk).exists()
        )
        freed = receipt.size_bytes + receipt.preview_size_bytes
        if email is not None and delete_email_object:
            storage.delete_object(email.raw_key)
            freed += email.size_bytes
            InboundEmail.objects.filter(pk=email.pk).update(raw_key='', size_bytes=0)
        # Receipts whose "possible duplicate of" pointed here are un-flagged by the FK's SET_NULL.
        deleted, _ = Receipt.objects.filter(pk=receipt.pk).delete()
        if deleted:
            add_stored_bytes(receipt.user_id, -freed)


@task(queue_name='receipts')
def delete_user_storage(user_id: str) -> None:
    """Delete every object stored for a (deleted) user, in one `delete_prefix`. Queued by the
    `pre_delete(User)` hook in `apps.receipts.apps`."""
    prefix = user_prefix(UUID(user_id))
    deleted = get_storage().delete_prefix(prefix)
    logger.info('Deleted %s receipts objects under %s', deleted, prefix)
