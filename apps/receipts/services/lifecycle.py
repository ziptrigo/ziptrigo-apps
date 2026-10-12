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

    The S3 deletes come first and are idempotent, and the database changes happen in one
    transaction afterwards, so a failure anywhere leaves everything in place for the next run (the
    `purge_trash` job, or the user clicking "delete forever" again) to finish.
    """
    storage = get_storage()
    for key in (receipt.file_key, receipt.preview_key):
        if key:
            storage.delete_object(key)

    email = receipt.inbound_email
    delete_email_object = False
    if email is not None and email.raw_key:
        delete_email_object = (
            not Receipt.objects.filter(inbound_email=email).exclude(pk=receipt.pk).exists()
        )
        if delete_email_object:
            storage.delete_object(email.raw_key)

    with transaction.atomic():
        freed = receipt.size_bytes + receipt.preview_size_bytes
        if email is not None and delete_email_object:
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
