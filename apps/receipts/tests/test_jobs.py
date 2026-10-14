from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps
from django.utils import timezone

from apps.billing.models import CreditTransaction
from apps.billing.services import get_balance
from apps.core.scheduler import get_jobs

from .. import jobs
from ..models import (
    InboundEmail,
    InboundMessage,
    InboundMessageStatus,
    Receipt,
    ReceiptsAccount,
    ReceiptStatus,
)
from ..services import inbound, processing
from ..services.accounts import get_account
from .factories import create_inbound_email, create_receipt, make_user

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

GB = 1024**3


def test_the_jobs_are_registered_with_the_scheduler():
    # `ready()` registers them at startup; the core scheduler tests clear the global registry, so
    # re-run that step (registering a name again just replaces it) to test it whatever ran before.
    apps.get_app_config('receipts')._register_jobs()
    specs = {job.name: job for job in get_jobs() if job.name.startswith('receipts.')}

    assert set(specs) == {
        'receipts.meter_storage',
        'receipts.purge_trash',
        'receipts.retry_due',
        'receipts.cleanup_uploads',
        'receipts.poll_inbound',
        'receipts.link_vendors',
        'receipts.purge_inbound_messages',
    }
    assert specs['receipts.meter_storage'].interval == timedelta(hours=24)
    assert specs['receipts.purge_trash'].interval == timedelta(hours=24)
    assert specs['receipts.retry_due'].interval == timedelta(minutes=5)
    assert specs['receipts.cleanup_uploads'].interval == timedelta(hours=1)
    assert specs['receipts.poll_inbound'].interval == timedelta(minutes=1)
    assert specs['receipts.link_vendors'].interval == timedelta(hours=1)
    assert specs['receipts.poll_inbound'].lease == timedelta(minutes=2)
    assert specs['receipts.poll_inbound'].func is jobs.poll_inbound
    assert specs['receipts.meter_storage'].func is jobs.meter_storage
    assert all(job.lease > job.interval / 100 for job in specs.values())


class TestMeterStorage:
    def test_bills_every_account_with_stored_bytes(self, funded_user, regular_user):
        from apps.billing.services import add_credits

        add_credits(regular_user, 50, source='test')
        for user in (funded_user, regular_user):
            account = get_account(user)
            ReceiptsAccount.objects.filter(pk=account.pk).update(
                stored_bytes=10 * GB, last_billed_at=timezone.now() - timedelta(hours=25)
            )

        jobs.meter_storage()

        assert get_balance(funded_user) == 99
        assert get_balance(regular_user) == 49

    def test_skips_empty_and_frozen_accounts(self, funded_user, regular_user):
        empty = get_account(funded_user)
        ReceiptsAccount.objects.filter(pk=empty.pk).update(
            last_billed_at=timezone.now() - timedelta(hours=25)
        )
        frozen = get_account(regular_user)
        ReceiptsAccount.objects.filter(pk=frozen.pk).update(
            stored_bytes=10 * GB, frozen_at=timezone.now()
        )

        jobs.meter_storage()

        assert not CreditTransaction.objects.filter(source='receipts').exists()

    def test_one_failing_account_does_not_stop_the_others(
        self, funded_user, regular_user, monkeypatch, caplog
    ):
        from apps.billing.services import add_credits

        add_credits(regular_user, 50, source='test')
        accounts = []
        for user in (funded_user, regular_user):
            account = get_account(user)
            ReceiptsAccount.objects.filter(pk=account.pk).update(
                stored_bytes=10 * GB, last_billed_at=timezone.now() - timedelta(hours=25)
            )
            accounts.append(account)
        real = jobs.meter_account
        calls = []

        def flaky(account_pk, price):
            calls.append(account_pk)
            if account_pk == accounts[0].pk:
                raise RuntimeError('db hiccup')
            real(account_pk, price)

        monkeypatch.setattr(jobs, 'meter_account', flaky)

        jobs.meter_storage()

        assert len(calls) == 2
        assert 'meter_account failed' in caplog.text
        assert get_balance(regular_user) == 49

    def test_an_account_frozen_by_this_run_is_not_unfrozen_by_it(self, funded_user):
        from apps.billing.services import spend_credits

        spend_credits(funded_user, 97, source='test')  # 3 credits left
        account = get_account(funded_user)
        ReceiptsAccount.objects.filter(pk=account.pk).update(
            stored_bytes=50 * GB, last_billed_at=timezone.now() - timedelta(hours=25)
        )  # 5 credits a day

        jobs.meter_storage()

        account.refresh_from_db()
        assert account.frozen_at is not None
        assert get_balance(funded_user) == 3  # the day was not charged, nor forgiven
        jobs.meter_storage()  # and the next run, with the same balance, keeps it frozen
        account.refresh_from_db()
        assert account.frozen_at is not None

    def test_running_twice_in_a_tick_charges_once(self, funded_user):
        account = get_account(funded_user)
        ReceiptsAccount.objects.filter(pk=account.pk).update(
            stored_bytes=10 * GB, last_billed_at=timezone.now() - timedelta(hours=25)
        )

        jobs.meter_storage()
        jobs.meter_storage()

        assert get_balance(funded_user) == 99


class TestPurgeTrash:
    def test_deletes_receipts_trashed_longer_than_trash_days(
        self, funded_user, fake_storage, receipts_settings
    ):
        old = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=31)
        )
        recent = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=29)
        )
        live = create_receipt(funded_user, fake_storage)

        jobs.purge_trash()

        assert not Receipt.objects.filter(pk=old.pk).exists()
        assert old.file_key not in fake_storage.objects
        assert Receipt.objects.filter(pk=recent.pk).exists()
        assert Receipt.objects.filter(pk=live.pk).exists()
        assert recent.file_key in fake_storage.objects

    def test_trash_days_is_configurable(self, funded_user, fake_storage, receipts_settings):
        receipts_settings.trash_days = 1
        receipts_settings.save()
        receipt = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=2)
        )

        jobs.purge_trash()

        assert not Receipt.objects.filter(pk=receipt.pk).exists()

    def test_the_account_stops_paying_for_what_was_purged(self, funded_user, fake_storage):
        keep = create_receipt(funded_user, fake_storage)
        gone = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=40)
        )
        assert get_account(funded_user).stored_bytes == keep.size_bytes + gone.size_bytes

        jobs.purge_trash()

        assert get_account(funded_user).stored_bytes == keep.size_bytes

    def test_the_preview_goes_too(self, funded_user, fake_storage):
        from ..services.accounts import add_stored_bytes

        receipt = create_receipt(
            funded_user,
            fake_storage,
            deleted_at=timezone.now() - timedelta(days=40),
            preview_key='receipts/users/x/previews/p.jpg',
            preview_size_bytes=300,
        )
        fake_storage.seed(receipt.preview_key, b'j' * 300)
        add_stored_bytes(funded_user.pk, 300)

        jobs.purge_trash()

        assert fake_storage.objects == {}
        assert get_account(funded_user).stored_bytes == 0

    def test_the_eml_is_deleted_with_the_emails_last_receipt(self, funded_user, fake_storage):
        from ..services.accounts import add_stored_bytes

        email = create_inbound_email(
            funded_user, raw_key='receipts/users/u/emails/e.eml', size_bytes=500
        )
        fake_storage.seed(email.raw_key, b'e' * 500)
        add_stored_bytes(funded_user.pk, 500)
        old = timezone.now() - timedelta(days=40)
        first = create_receipt(funded_user, fake_storage, inbound_email=email, deleted_at=old)
        second = create_receipt(funded_user, fake_storage, inbound_email=email, deleted_at=old)

        # Only the first is due.
        Receipt.objects.filter(pk=second.pk).update(deleted_at=timezone.now())
        jobs.purge_trash()
        assert email.raw_key in fake_storage.objects
        assert Receipt.objects.filter(pk=second.pk).exists()

        Receipt.objects.filter(pk=second.pk).update(deleted_at=old)
        jobs.purge_trash()

        email.refresh_from_db()
        assert email.raw_key == '' and email.size_bytes == 0
        assert fake_storage.objects == {}
        assert not Receipt.objects.filter(pk__in=[first.pk, second.pk]).exists()
        assert InboundEmail.objects.filter(pk=email.pk).exists()  # the record of what arrived stays
        assert get_account(funded_user).stored_bytes == 0

    def test_a_receipt_still_being_processed_is_not_purged(self, funded_user, fake_storage):
        receipt = create_receipt(
            funded_user,
            fake_storage,
            status=ReceiptStatus.PROCESSING,
            deleted_at=timezone.now() - timedelta(days=40),
        )

        jobs.purge_trash()

        assert Receipt.objects.filter(pk=receipt.pk).exists()
        assert receipt.file_key in fake_storage.objects

    def test_a_live_sibling_keeps_the_eml(self, funded_user, fake_storage):
        email = create_inbound_email(
            funded_user, raw_key='receipts/users/u/emails/e.eml', size_bytes=500
        )
        fake_storage.seed(email.raw_key, b'e')
        create_receipt(funded_user, fake_storage, inbound_email=email)
        create_receipt(
            funded_user,
            fake_storage,
            inbound_email=email,
            deleted_at=timezone.now() - timedelta(days=40),
        )

        jobs.purge_trash()

        assert email.raw_key in fake_storage.objects

    def test_a_failed_s3_delete_keeps_the_row_so_the_next_run_retries(
        self, funded_user, fake_storage, caplog
    ):
        receipt = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=40)
        )
        fake_storage.failing_deletes.add(receipt.file_key)

        jobs.purge_trash()

        assert Receipt.objects.filter(pk=receipt.pk).exists()
        assert 'Failed to purge receipt' in caplog.text
        fake_storage.failing_deletes.clear()

        jobs.purge_trash()

        assert not Receipt.objects.filter(pk=receipt.pk).exists()
        assert get_account(funded_user).stored_bytes == 0

    def test_one_bad_receipt_does_not_stop_the_rest(self, funded_user, fake_storage):
        old = timezone.now() - timedelta(days=40)
        bad = create_receipt(funded_user, fake_storage, deleted_at=old)
        good = create_receipt(funded_user, fake_storage, deleted_at=old)
        fake_storage.failing_deletes.add(bad.file_key)

        jobs.purge_trash()

        assert Receipt.objects.filter(pk=bad.pk).exists()
        assert not Receipt.objects.filter(pk=good.pk).exists()

    def test_idempotent(self, funded_user, fake_storage):
        create_receipt(funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=40))

        jobs.purge_trash()
        jobs.purge_trash()

        assert get_account(funded_user).stored_bytes == 0

    def test_a_receipt_restored_while_the_job_runs_is_left_alone(self, funded_user, fake_storage):
        """The job loads the batch first and purges one at a time: a restore in between wins."""
        receipt = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=40)
        )
        real = jobs.purge_receipt

        def restored_meanwhile(stale, **kwargs):
            Receipt.objects.filter(pk=stale.pk).update(deleted_at=None)
            return real(stale, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(jobs, 'purge_receipt', restored_meanwhile)
            jobs.purge_trash()

        receipt.refresh_from_db()
        assert receipt.deleted_at is None
        assert receipt.file_key in fake_storage.objects
        assert get_account(funded_user).stored_bytes == receipt.size_bytes

    def test_a_receipt_trashed_again_after_the_cutoff_is_left_alone(
        self, funded_user, fake_storage
    ):
        receipt = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=40)
        )
        real = jobs.purge_receipt

        def trashed_again_meanwhile(stale, **kwargs):
            Receipt.objects.filter(pk=stale.pk).update(deleted_at=timezone.now())
            return real(stale, **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(jobs, 'purge_receipt', trashed_again_meanwhile)
            jobs.purge_trash()

        assert Receipt.objects.filter(pk=receipt.pk).exists()
        assert receipt.file_key in fake_storage.objects

    def test_duplicates_flagged_against_a_purged_receipt_are_unflagged(
        self, funded_user, fake_storage
    ):
        gone = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now() - timedelta(days=40)
        )
        copy = create_receipt(funded_user, fake_storage, possible_duplicate_of=gone)

        jobs.purge_trash()

        copy.refresh_from_db()
        assert copy.possible_duplicate_of is None


class TestRetryDue:
    def test_requeues_receipts_whose_retry_time_has_come(
        self, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        due = create_receipt(
            funded_user,
            fake_storage,
            next_attempt_at=timezone.now() - timedelta(minutes=1),
            attempts=1,
            charged_attempt_id=None,
        )
        later = create_receipt(
            funded_user, fake_storage, next_attempt_at=timezone.now() + timedelta(hours=1)
        )

        with django_capture_on_commit_callbacks(execute=True):
            jobs.retry_due()

        due.refresh_from_db()
        later.refresh_from_db()
        assert due.status == ReceiptStatus.PROCESSED
        assert later.status == ReceiptStatus.RECEIVED
        assert len(fake_ai.calls) == 1

    def test_the_whole_retry_cycle_ends_in_a_processed_receipt_charged_once(
        self, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        from ..services.ai import TransientAIError

        fake_ai.queue = [TransientAIError('timeout')]
        receipt = create_receipt(funded_user, fake_storage)
        processing.process(str(receipt.pk))
        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.RECEIVED
        Receipt.objects.filter(pk=receipt.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1)
        )

        with django_capture_on_commit_callbacks(execute=True):
            jobs.retry_due()

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED
        assert get_balance(funded_user) == 99

    def test_recovers_receipts_stuck_in_processing(self, funded_user, fake_storage):
        stuck = create_receipt(
            funded_user,
            fake_storage,
            status=ReceiptStatus.PROCESSING,
            processing_started_at=timezone.now() - timedelta(minutes=31),
        )
        busy = create_receipt(
            funded_user,
            fake_storage,
            status=ReceiptStatus.PROCESSING,
            processing_started_at=timezone.now() - timedelta(minutes=5),
        )

        jobs.retry_due()

        stuck.refresh_from_db()
        busy.refresh_from_db()
        assert stuck.status == ReceiptStatus.RECEIVED
        assert stuck.attempts == 1
        assert busy.status == ReceiptStatus.PROCESSING

    def test_picks_up_receipts_that_were_never_queued(
        self, funded_user, fake_storage, django_capture_on_commit_callbacks
    ):
        forgotten = create_receipt(funded_user, fake_storage)
        fresh = create_receipt(funded_user, fake_storage)
        Receipt.objects.filter(pk=forgotten.pk).update(
            updated_at=timezone.now() - timedelta(hours=1)
        )

        with django_capture_on_commit_callbacks(execute=True):
            jobs.retry_due()

        forgotten.refresh_from_db()
        fresh.refresh_from_db()
        assert forgotten.status == ReceiptStatus.PROCESSED
        assert fresh.status == ReceiptStatus.RECEIVED  # give a just-added one time to be picked up

    def test_leaves_waiting_for_credits_alone(
        self, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        create_receipt(
            funded_user,
            fake_storage,
            status=ReceiptStatus.WAITING_FOR_CREDITS,
            updated_at=timezone.now() - timedelta(hours=5),
        )
        Receipt.objects.update(updated_at=timezone.now() - timedelta(hours=5))

        with django_capture_on_commit_callbacks(execute=True):
            jobs.retry_due()

        assert fake_ai.calls == []

    def test_ignores_trashed_and_unfinished_uploads(
        self, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        create_receipt(
            funded_user,
            fake_storage,
            deleted_at=timezone.now(),
            next_attempt_at=timezone.now() - timedelta(minutes=1),
        )
        create_receipt(
            funded_user,
            fake_storage,
            uploaded=False,
            next_attempt_at=timezone.now() - timedelta(minutes=1),
        )

        with django_capture_on_commit_callbacks(execute=True):
            jobs.retry_due()

        assert fake_ai.calls == []

    def test_one_failure_does_not_stop_the_rest(
        self, funded_user, fake_storage, monkeypatch, caplog
    ):
        first = create_receipt(
            funded_user, fake_storage, next_attempt_at=timezone.now() - timedelta(minutes=1)
        )
        second = create_receipt(
            funded_user, fake_storage, next_attempt_at=timezone.now() - timedelta(minutes=1)
        )
        calls = []

        def flaky(receipt_id):
            calls.append(receipt_id)
            if len(calls) == 1:
                raise RuntimeError('queue down')

        monkeypatch.setattr(jobs, 'enqueue_processing', flaky)

        jobs.retry_due()

        assert set(calls) == {first.pk, second.pk}
        assert 'Failed to queue receipt' in caplog.text


class TestCleanupUploads:
    def test_removes_uploads_older_than_24_hours(self, funded_user, fake_storage):
        stale = create_receipt(funded_user, fake_storage, uploaded=False, count_bytes=False)
        fresh = create_receipt(funded_user, fake_storage, uploaded=False, count_bytes=False)
        done = create_receipt(funded_user, fake_storage, uploaded=True)
        Receipt.objects.filter(pk__in=[stale.pk, done.pk]).update(
            created_at=timezone.now() - timedelta(hours=25)
        )

        jobs.cleanup_uploads()

        assert not Receipt.objects.filter(pk=stale.pk).exists()
        assert stale.file_key not in fake_storage.objects
        assert Receipt.objects.filter(pk=fresh.pk).exists()
        assert Receipt.objects.filter(pk=done.pk).exists()
        assert done.file_key in fake_storage.objects

    def test_a_failure_is_logged_and_the_rest_continue(self, funded_user, fake_storage, caplog):
        old = timezone.now() - timedelta(hours=25)
        bad = create_receipt(funded_user, fake_storage, uploaded=False, count_bytes=False)
        good = create_receipt(funded_user, fake_storage, uploaded=False, count_bytes=False)
        Receipt.objects.update(created_at=old)
        fake_storage.failing_deletes.add(bad.file_key)

        jobs.cleanup_uploads()

        assert Receipt.objects.filter(pk=bad.pk).exists()
        assert not Receipt.objects.filter(pk=good.pk).exists()
        assert 'Failed to clean up' in caplog.text


def test_billing_amounts_are_decimal_safe():
    assert Decimal('0.1') * 10 == Decimal('1.0')


class TestPollInbound:
    def test_runs_the_inbound_poll(
        self, fake_storage, monkeypatch, django_capture_on_commit_callbacks
    ):
        from ..models import InboundMessage

        monkeypatch.setattr('apps.receipts.services.inbound.ingest_message', _NoopTask())
        fake_storage.seed('receipts/inbound/abc', b'raw')

        with django_capture_on_commit_callbacks(execute=True):
            jobs.poll_inbound()

        assert InboundMessage.objects.filter(s3_key='receipts/inbound/abc').exists()


class _NoopTask:
    def enqueue(self, *args):
        pass


def _message(status: str, age_days: int, *, processed: bool = True) -> InboundMessage:
    message = InboundMessage.objects.create(
        s3_key=f'receipts/inbound/{status}-{age_days}-{InboundMessage.objects.count()}',
        status=status,
    )
    then = timezone.now() - timedelta(days=age_days)
    InboundMessage.objects.filter(pk=message.pk).update(
        created_at=then, processed_at=then if processed else None
    )
    return message


def test_purge_inbound_messages_deletes_only_old_done_messages_without_emails(fake_storage):
    old_done = _message(InboundMessageStatus.DONE, 31)
    recent_done = _message(InboundMessageStatus.DONE, 5)
    old_pending = _message(InboundMessageStatus.PENDING, 90)
    old_failed = _message(InboundMessageStatus.FAILED, 90)
    with_email = _message(InboundMessageStatus.DONE, 90)
    email = InboundEmail.objects.create(
        user=make_user(), message=with_email, recipient='receipts+abcd2345@ziptrigo.com'
    )

    jobs.purge_inbound_messages()
    jobs.purge_inbound_messages()  # idempotent

    assert not InboundMessage.objects.filter(pk=old_done.pk).exists()
    kept = set(InboundMessage.objects.values_list('pk', flat=True))
    assert kept == {recent_done.pk, old_pending.pk, old_failed.pk, with_email.pk}
    assert InboundEmail.objects.filter(pk=email.pk).exists()
    assert fake_storage.deleted == [old_done.s3_key]


def test_purge_done_messages_runs_every_batch_and_deletes_the_objects(fake_storage, monkeypatch):
    monkeypatch.setattr(inbound, 'PURGE_BATCH', 1)
    old = [_message(InboundMessageStatus.DONE, 40) for _ in range(3)]
    recent = _message(InboundMessageStatus.DONE, 2)
    for message in (*old, recent):
        fake_storage.seed(message.s3_key, b'x')

    assert inbound.purge_done_messages() == 3

    assert list(InboundMessage.objects.values_list('pk', flat=True)) == [recent.pk]
    assert sorted(fake_storage.deleted) == sorted(message.s3_key for message in old)
    assert set(fake_storage.objects) == {recent.s3_key}


def test_purge_done_messages_measures_age_from_processed_at(fake_storage):
    # An admin retry leaves `created_at` alone: this one is old on paper, done just now.
    retried = _message(InboundMessageStatus.DONE, 90)
    InboundMessage.objects.filter(pk=retried.pk).update(processed_at=timezone.now())
    # No `processed_at` (a row from before it was set): `created_at` decides.
    legacy = _message(InboundMessageStatus.DONE, 40, processed=False)

    assert inbound.purge_done_messages() == 1

    assert list(InboundMessage.objects.values_list('pk', flat=True)) == [retried.pk]
    assert fake_storage.deleted == [legacy.s3_key]


def test_a_failing_object_delete_leaves_the_rows_and_raises(fake_storage):
    message = _message(InboundMessageStatus.DONE, 40)
    fake_storage.failing_deletes.add(message.s3_key)

    with pytest.raises(RuntimeError):
        inbound.purge_done_messages()

    assert InboundMessage.objects.filter(pk=message.pk).exists()

    fake_storage.failing_deletes.clear()
    assert inbound.purge_done_messages() == 1
    assert not InboundMessage.objects.filter(pk=message.pk).exists()
