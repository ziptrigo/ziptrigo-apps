from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import BillingSettings, CreditTransaction, CreditTransactionType
from apps.billing.services import add_credits, get_balance, spend_credits

from ..models import Receipt, ReceiptsAccount, ReceiptStatus
from ..services import metering
from ..services.accounts import add_stored_bytes, get_account
from .factories import create_receipt

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

GB = 1024**3


@pytest.fixture
def stored(account):
    """The account holds 10 GB and is due for billing. At the default 0.1 credits/GB/day that is
    exactly one credit a day."""

    def _set(gigabytes: int = 10, *, hours_ago: int = 25) -> ReceiptsAccount:
        ReceiptsAccount.objects.filter(pk=account.pk).update(
            stored_bytes=gigabytes * GB, last_billed_at=timezone.now() - timedelta(hours=hours_ago)
        )
        account.refresh_from_db()
        return account

    return _set


def storage_spends(user):
    return CreditTransaction.objects.filter(
        user=user, source='receipts', type=CreditTransactionType.SPEND
    )


class TestMeterAccount:
    def test_charges_a_day_of_storage_at_the_shared_price(
        self, stored, funded_user, billing_settings
    ):
        account = stored(10)
        due_since = account.last_billed_at

        metering.meter_account(account.pk)

        account.refresh_from_db()
        assert get_balance(funded_user) == 99
        assert account.billed_days == 1
        assert account.accrued == Decimal('0')
        # Advances by exactly one period, not to "now".
        assert account.last_billed_at == due_since + timedelta(hours=24)
        tx = storage_spends(funded_user).get()
        assert tx.amount == -1
        assert tx.description == 'Receipts storage, day 1'

    def test_the_price_is_the_shared_billing_setting(self, stored, funded_user, billing_settings):
        BillingSettings.objects.filter(pk=1).update(storage_price_per_gb_per_day=Decimal('0.5'))
        account = stored(10)

        metering.meter_account(account.pk)

        assert get_balance(funded_user) == 95

    def test_fractions_are_carried_until_a_whole_credit(self, stored, funded_user):
        account = stored(1)  # 0.1 credit a day

        for day in range(1, 10):
            ReceiptsAccount.objects.filter(pk=account.pk).update(
                last_billed_at=timezone.now() - timedelta(hours=25)
            )
            metering.meter_account(account.pk)
            account.refresh_from_db()
            assert account.accrued == Decimal('0.1') * day
            assert get_balance(funded_user) == 100

        ReceiptsAccount.objects.filter(pk=account.pk).update(
            last_billed_at=timezone.now() - timedelta(hours=25)
        )
        metering.meter_account(account.pk)

        account.refresh_from_db()
        assert get_balance(funded_user) == 99
        assert account.accrued == Decimal('0')
        assert account.billed_days == 10

    def test_not_charged_again_before_the_period_is_up(self, stored, funded_user):
        account = stored(10, hours_ago=2)

        metering.meter_account(account.pk)

        assert get_balance(funded_user) == 100
        account.refresh_from_db()
        assert account.billed_days == 0

    def test_a_second_run_of_the_same_tick_does_not_charge_twice(self, stored, funded_user):
        account = stored(10)

        metering.meter_account(account.pk)
        metering.meter_account(account.pk)

        assert get_balance(funded_user) == 99

    def test_an_overdue_account_catches_up_one_day_per_run(self, stored, funded_user):
        account = stored(10, hours_ago=24 * 3 + 1)

        metering.meter_account(account.pk)
        metering.meter_account(account.pk)

        assert get_balance(funded_user) == 98

    def test_trashed_receipts_still_count_toward_storage(self, account, funded_user, fake_storage):
        trashed = create_receipt(
            funded_user, fake_storage, deleted_at=timezone.now(), data=b'\xff\xd8\xff' + b'0' * 1000
        )

        assert get_account(funded_user).stored_bytes == trashed.size_bytes

    def test_not_enough_credits_freezes_the_account_and_emails_the_owner(
        self, stored, funded_user, sent_emails, django_capture_on_commit_callbacks
    ):
        spend_credits(funded_user, 100, source='test')
        account = stored(10)
        due_since = account.last_billed_at

        with django_capture_on_commit_callbacks(execute=True):
            metering.meter_account(account.pk)

        account.refresh_from_db()
        assert account.frozen_at is not None
        assert account.last_billed_at == due_since  # the clock stops
        assert account.billed_days == 0
        assert not storage_spends(funded_user).exists()
        [mail] = sent_emails
        assert mail['to'] == funded_user.email
        assert mail['subject'] == 'Your receipts storage is paused'
        assert 'nothing was deleted' in mail['text_body']

    def test_a_frozen_account_is_not_metered(self, stored, funded_user):
        account = stored(10)
        ReceiptsAccount.objects.filter(pk=account.pk).update(frozen_at=timezone.now())

        metering.meter_account(account.pk)

        assert get_balance(funded_user) == 100

    def test_freezing_twice_sends_one_email(
        self, stored, funded_user, sent_emails, django_capture_on_commit_callbacks
    ):
        spend_credits(funded_user, 100, source='test')
        account = stored(10)

        with django_capture_on_commit_callbacks(execute=True):
            metering.freeze_account(account)
            metering.freeze_account(ReceiptsAccount.objects.get(pk=account.pk))

        assert len(sent_emails) == 1


class TestUnfreeze:
    def freeze(self, stored, user):
        spend_credits(user, 100, source='test')
        account = stored(10)
        metering.meter_account(account.pk)
        account.refresh_from_db()
        assert account.frozen_at is not None
        return account

    def test_topping_up_unfreezes_without_back_billing(
        self, stored, funded_user, django_capture_on_commit_callbacks
    ):
        account = self.freeze(stored, funded_user)
        # It has been frozen for ten days.
        ReceiptsAccount.objects.filter(pk=account.pk).update(
            last_billed_at=timezone.now() - timedelta(days=10)
        )

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 50, source='test')

        account.refresh_from_db()
        assert account.frozen_at is None
        assert timezone.now() - account.last_billed_at < timedelta(minutes=1)
        metering.meter_account(account.pk)
        assert get_balance(funded_user) == 50  # nothing billed for the frozen days

    def test_less_than_one_credit_does_not_unfreeze(self, stored, funded_user):
        account = self.freeze(stored, funded_user)

        assert metering.resume(str(funded_user.pk)) == 0

        account.refresh_from_db()
        assert account.frozen_at is not None

    def test_a_balance_of_exactly_one_credit_unfreezes(self, stored, funded_user):
        account = self.freeze(stored, funded_user)
        add_credits(funded_user, 1, source='test')

        metering.resume(str(funded_user.pk))

        account.refresh_from_db()
        assert account.frozen_at is None

    def test_a_balance_below_a_days_storage_does_not_unfreeze(self, stored, funded_user):
        # 50 GB at 0.1 credit/GB/day is 5 credits a day: 1-4 credits cannot pay the next run.
        spend_credits(funded_user, 100, source='test')
        account = stored(50)
        metering.meter_account(account.pk)
        account.refresh_from_db()
        assert account.frozen_at is not None
        add_credits(funded_user, 4, source='test')

        assert metering.resume(str(funded_user.pk)) == 0

        account.refresh_from_db()
        assert account.frozen_at is not None

    def test_a_balance_that_covers_a_days_storage_unfreezes(self, stored, funded_user):
        spend_credits(funded_user, 100, source='test')
        account = stored(50)
        metering.meter_account(account.pk)
        add_credits(funded_user, 5, source='test')

        metering.resume(str(funded_user.pk))

        account.refresh_from_db()
        assert account.frozen_at is None

    def test_the_daily_cost_rounds_up_and_is_at_least_one_credit(self, billing_settings):
        assert metering.daily_storage_cost(0) == 1
        assert metering.daily_storage_cost(GB) == 1  # 0.1 credit
        assert metering.daily_storage_cost(10 * GB) == 1
        assert metering.daily_storage_cost(11 * GB) == 2  # 1.1 credits
        assert metering.daily_storage_cost(50 * GB) == 5
        assert metering.daily_storage_cost(10 * GB, Decimal('0.5')) == 5

    def test_nothing_is_deleted_while_frozen(self, stored, funded_user, make_receipt, fake_storage):
        receipt = make_receipt()
        self.freeze(stored, funded_user)

        assert Receipt.objects.filter(pk=receipt.pk).exists()
        assert receipt.file_key in fake_storage.objects

    def test_the_daily_job_resumes_a_frozen_account_that_has_topped_up(self, stored, funded_user):
        from .. import jobs

        account = self.freeze(stored, funded_user)
        add_credits(funded_user, 5, source='test')  # (no on_commit in a test transaction)

        jobs.meter_storage()

        account.refresh_from_db()
        assert account.frozen_at is None


class TestResume:
    def waiting(self, user, fake_storage, count):
        return [
            create_receipt(user, fake_storage, status=ReceiptStatus.WAITING_FOR_CREDITS)
            for _ in range(count)
        ]

    def test_unknown_user_and_user_without_an_account(self, user):
        assert metering.resume('00000000-0000-0000-0000-000000000000') == 0
        assert metering.resume('not-a-uuid') == 0
        assert metering.resume(str(user.pk)) == 0

    def test_waiting_receipts_are_processed_oldest_first_and_stop_at_the_first_unaffordable(
        self, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        spend_credits(funded_user, 98, source='test')  # 2 credits left
        first, second, third = self.waiting(funded_user, fake_storage, 3)

        with django_capture_on_commit_callbacks(execute=True):
            assert metering.resume(str(funded_user.pk)) == 2

        for receipt in (first, second, third):
            receipt.refresh_from_db()
        assert first.status == ReceiptStatus.PROCESSED
        assert second.status == ReceiptStatus.PROCESSED
        assert third.status == ReceiptStatus.WAITING_FOR_CREDITS
        assert len(fake_ai.calls) == 2
        assert get_balance(funded_user) == 0

    def test_the_oldest_receipt_goes_first(
        self, funded_user, fake_storage, django_capture_on_commit_callbacks
    ):
        spend_credits(funded_user, 99, source='test')  # 1 credit left
        older, newer = self.waiting(funded_user, fake_storage, 2)
        Receipt.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=1))

        with django_capture_on_commit_callbacks(execute=True):
            metering.resume(str(funded_user.pk))

        older.refresh_from_db()
        newer.refresh_from_db()
        assert older.status == ReceiptStatus.PROCESSED
        assert newer.status == ReceiptStatus.WAITING_FOR_CREDITS

    def test_free_processing_resumes_everything(
        self, funded_user, fake_storage, receipts_settings, django_capture_on_commit_callbacks
    ):
        receipts_settings.price_per_file = 0
        receipts_settings.save()
        spend_credits(funded_user, 100, source='test')
        receipts = self.waiting(funded_user, fake_storage, 3)

        with django_capture_on_commit_callbacks(execute=True):
            assert metering.resume(str(funded_user.pk)) == 3

        assert all(Receipt.objects.get(pk=r.pk).status == ReceiptStatus.PROCESSED for r in receipts)

    def test_an_already_paid_attempt_costs_nothing_to_resume(
        self, funded_user, fake_storage, django_capture_on_commit_callbacks
    ):
        spend_credits(funded_user, 100, source='test')
        receipt = create_receipt(
            funded_user, fake_storage, status=ReceiptStatus.WAITING_FOR_CREDITS
        )
        Receipt.objects.filter(pk=receipt.pk).update(charged_attempt_id=receipt.attempt_id)

        with django_capture_on_commit_callbacks(execute=True):
            assert metering.resume(str(funded_user.pk)) == 1

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED
        assert get_balance(funded_user) == 0

    def test_a_trashed_waiting_receipt_is_skipped(
        self, funded_user, fake_storage, django_capture_on_commit_callbacks
    ):
        create_receipt(
            funded_user,
            fake_storage,
            status=ReceiptStatus.WAITING_FOR_CREDITS,
            deleted_at=timezone.now(),
        )

        with django_capture_on_commit_callbacks(execute=True):
            assert metering.resume(str(funded_user.pk)) == 0

    def test_a_frozen_account_is_unfrozen_then_its_waiting_receipts_run(
        self, funded_user, fake_storage, django_capture_on_commit_callbacks
    ):
        account = get_account(funded_user)
        ReceiptsAccount.objects.filter(pk=account.pk).update(frozen_at=timezone.now())
        [receipt] = self.waiting(funded_user, fake_storage, 1)

        with django_capture_on_commit_callbacks(execute=True):
            metering.resume(str(funded_user.pk))

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.PROCESSED
        assert get_account(funded_user).frozen_at is None

    def test_resume_is_idempotent(
        self, funded_user, fake_storage, fake_ai, django_capture_on_commit_callbacks
    ):
        self.waiting(funded_user, fake_storage, 1)

        with django_capture_on_commit_callbacks(execute=True):
            metering.resume(str(funded_user.pk))
            metering.resume(str(funded_user.pk))

        assert len(fake_ai.calls) == 1
        assert get_balance(funded_user) == 99

    def test_the_resume_task_is_on_the_receipts_queue(self):
        assert metering.resume_account.queue_name == 'receipts'


class FakeTask:
    """Stands in for a `django.tasks` task (a frozen dataclass, so its `enqueue` can't be patched)."""

    def __init__(self, calls, error=None):
        self.calls = calls
        self.error = error

    def enqueue(self, *args):
        if self.error:
            self.error()
        self.calls.append(args)


class TestCreditsAddedReceiver:
    def test_a_top_up_for_a_user_without_receipts_does_nothing(
        self, user, monkeypatch, django_capture_on_commit_callbacks
    ):
        calls = []
        monkeypatch.setattr(metering, 'resume_account', FakeTask(calls))

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 5, source='test')

        assert calls == []

    def test_a_top_up_queues_resume_account_with_the_plain_user_id(
        self, funded_user, account, monkeypatch, django_capture_on_commit_callbacks
    ):
        calls = []
        monkeypatch.setattr(metering, 'resume_account', FakeTask(calls))

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 5, source='test')

        assert calls == [(str(funded_user.pk),)]

    def test_a_failing_receiver_never_stops_the_top_up(
        self, funded_user, account, monkeypatch, django_capture_on_commit_callbacks, caplog
    ):
        def boom():
            raise RuntimeError('queue down')

        monkeypatch.setattr(metering, 'resume_account', FakeTask([], boom))

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 5, source='test')  # must not raise

        assert get_balance(funded_user) == 105
        assert 'Could not queue resume_account' in caplog.text

    def test_a_refund_is_a_credits_added_too_and_resuming_is_harmless(
        self, make_receipt, funded_user, fake_ai, django_capture_on_commit_callbacks
    ):
        from ..services import processing
        from ..services.ai import PermanentAIError

        fake_ai.queue = [PermanentAIError('x')]
        receipt = make_receipt()

        with django_capture_on_commit_callbacks(execute=True):
            processing.process(str(receipt.pk))

        receipt.refresh_from_db()
        assert receipt.status == ReceiptStatus.FAILED
        assert get_balance(funded_user) == 100


def test_add_stored_bytes_restarts_the_clock_for_an_empty_account(account, funded_user):
    ReceiptsAccount.objects.filter(pk=account.pk).update(
        last_billed_at=timezone.now() - timedelta(days=30)
    )

    add_stored_bytes(funded_user.pk, 1000)

    account.refresh_from_db()
    assert account.stored_bytes == 1000
    assert timezone.now() - account.last_billed_at < timedelta(minutes=1)


def test_add_stored_bytes_leaves_the_clock_alone_when_something_is_already_stored(
    account, funded_user
):
    add_stored_bytes(funded_user.pk, 1000)
    earlier = timezone.now() - timedelta(hours=5)
    ReceiptsAccount.objects.filter(pk=account.pk).update(last_billed_at=earlier)

    add_stored_bytes(funded_user.pk, 500)

    account.refresh_from_db()
    assert account.stored_bytes == 1500
    assert account.last_billed_at == earlier
