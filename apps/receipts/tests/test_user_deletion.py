"""Deleting a user deletes their receipts rows (by cascade) and, once the deletion has committed,
every object under their S3 prefix -- through a queued task that takes the plain id."""

import pytest
from django.db import transaction

from apps.accounts.models import User

from ..models import InboxCode, Receipt, ReceiptCategory, ReceiptsAccount
from ..services import lifecycle
from ..services.accounts import get_account
from ..services.storage import INBOUND_PREFIX, user_prefix
from .factories import create_receipt, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_deleting_a_user_deletes_their_objects_and_nobody_elses(
    funded_user, fake_storage, django_capture_on_commit_callbacks
):
    other = make_user()
    mine = create_receipt(funded_user, fake_storage)
    theirs = create_receipt(other, fake_storage)
    fake_storage.seed(f'{user_prefix(funded_user.pk)}previews/p.jpg', b'p')
    fake_storage.seed(f'{user_prefix(funded_user.pk)}emails/e.eml', b'e')
    fake_storage.seed(f'{INBOUND_PREFIX}some-message', b'raw')

    with django_capture_on_commit_callbacks(execute=True):
        funded_user.delete()

    assert set(fake_storage.objects) == {theirs.file_key, f'{INBOUND_PREFIX}some-message'}
    assert not Receipt.objects.filter(pk=mine.pk).exists()
    assert not User.objects.filter(pk=funded_user.pk).exists()
    assert not ReceiptsAccount.objects.filter(pk=funded_user.pk).exists()
    assert not ReceiptCategory.objects.filter(user_id=funded_user.pk).exists()
    assert not InboxCode.objects.filter(account_id=funded_user.pk).exists()


def test_the_deletion_is_queued_not_done_inline(
    funded_user, fake_storage, django_capture_on_commit_callbacks
):
    receipt = create_receipt(funded_user, fake_storage)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        funded_user.delete()

    assert receipt.file_key in fake_storage.objects  # nothing happens until the commit
    assert len(callbacks) >= 1


def test_a_rolled_back_deletion_keeps_the_objects(
    funded_user, fake_storage, django_capture_on_commit_callbacks
):
    receipt = create_receipt(funded_user, fake_storage)
    user_id = funded_user.pk

    with django_capture_on_commit_callbacks(execute=True):
        try:
            with transaction.atomic():
                funded_user.delete()
                raise RuntimeError('something else failed')
        except RuntimeError:
            pass

    assert receipt.file_key in fake_storage.objects
    assert User.objects.filter(pk=user_id).exists()


def test_the_task_takes_a_plain_id_and_uses_the_user_prefix(funded_user, fake_storage):
    create_receipt(funded_user, fake_storage)
    other = make_user()
    kept = create_receipt(other, fake_storage)

    lifecycle.delete_user_storage.call(str(funded_user.pk))

    assert set(fake_storage.objects) == {kept.file_key}


def test_the_task_runs_on_the_receipts_queue():
    assert lifecycle.delete_user_storage.queue_name == 'receipts'


def test_a_user_without_receipts_deletes_cleanly(user, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        user.delete()

    assert not User.objects.filter(pk=user.pk).exists()


def test_a_failure_queueing_the_cleanup_never_blocks_deleting_the_user(
    funded_user, monkeypatch, django_capture_on_commit_callbacks, caplog
):
    class Broken:
        def enqueue(self, *args):
            raise RuntimeError('queue down')

    monkeypatch.setattr(lifecycle, 'delete_user_storage', Broken())
    get_account(funded_user)
    user_id = funded_user.pk

    with django_capture_on_commit_callbacks(execute=True):
        funded_user.delete()  # must not raise

    assert not User.objects.filter(pk=user_id).exists()
    assert 'Could not queue' in caplog.text
