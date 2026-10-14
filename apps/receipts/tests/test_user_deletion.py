"""Deleting a user deletes their receipts rows (by cascade) and, once the deletion has committed,
every object under their S3 prefix -- through a queued task that takes the plain id."""

import pytest
from django.db import transaction
from django.utils import timezone

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


class _FlakyDeletes:
    """Make the fake's `delete_prefix` fail `failures` times (a per-key S3 error), then work."""

    def __init__(self, fake_storage, monkeypatch, failures):
        self.calls = 0
        self.failures = failures
        self.real = fake_storage.delete_prefix
        monkeypatch.setattr(fake_storage, 'delete_prefix', self)

    def __call__(self, prefix):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError('S3 could not delete 1 object(s): k: AccessDenied')
        return self.real(prefix)


def test_a_failed_cleanup_is_tried_again(funded_user, fake_storage, monkeypatch, caplog):
    """`delete_prefix` raises on a per-key S3 error, the queue never retries a failed task and the
    user row is gone, so the task queues itself again rather than leaking the objects."""
    receipt = create_receipt(funded_user, fake_storage)
    flaky = _FlakyDeletes(fake_storage, monkeypatch, failures=2)

    lifecycle.delete_user_storage.call(str(funded_user.pk))

    assert flaky.calls == 3
    assert receipt.file_key not in fake_storage.objects
    assert 'retrying' in caplog.text


def test_the_retry_is_deferred_when_the_backend_can_defer(funded_user, fake_storage, monkeypatch):
    from django.tasks.base import Task

    create_receipt(funded_user, fake_storage)
    _FlakyDeletes(fake_storage, monkeypatch, failures=1)
    monkeypatch.setattr(
        'django.tasks.backends.immediate.ImmediateBackend.supports_defer', True, raising=False
    )
    deferred = []
    real_using = Task.using

    def using(self, **kwargs):
        deferred.append(kwargs.get('run_after'))
        return real_using(self, **{k: v for k, v in kwargs.items() if k != 'run_after'})

    monkeypatch.setattr(Task, 'using', using)

    lifecycle.delete_user_storage.call(str(funded_user.pk))

    [run_after] = deferred
    delay = (run_after - timezone.now()).total_seconds()
    assert 0 < delay <= lifecycle.USER_STORAGE_RETRY_DELAYS[0].total_seconds()


def test_cleanup_gives_up_after_the_last_retry_and_says_where_the_objects_are(
    funded_user, fake_storage, monkeypatch, caplog
):
    receipt = create_receipt(funded_user, fake_storage)
    flaky = _FlakyDeletes(fake_storage, monkeypatch, failures=100)

    lifecycle.delete_user_storage.call(str(funded_user.pk))

    assert flaky.calls == len(lifecycle.USER_STORAGE_RETRY_DELAYS) + 1
    assert receipt.file_key in fake_storage.objects
    assert 'Giving up' in caplog.text
    assert user_prefix(funded_user.pk) in caplog.text
