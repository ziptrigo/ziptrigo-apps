"""Attachment storage billing (issue #184): the daily arithmetic, the freeze and its email and
notice, the resume on a top-up, the daily job's snapshot rule, and the `credits_added` wiring.
Mirrors Receipts' metering tests."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import CreditTransaction, CreditTransactionType
from apps.billing.services import (
    InsufficientCreditsError,
    add_credits,
    get_balance,
    spend_credits,
)
from apps.core.models import Notification
from apps.core.scheduler import get_jobs
from apps.core.services import notifications

from .. import jobs
from ..models import TodoAccount
from ..services import get_account, metering
from ..services.accounts import add_stored_bytes
from .factories import make_attachment, make_task

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

GB = 1024**3


@pytest.fixture
def account(funded_user):
    return get_account(funded_user)


@pytest.fixture
def stored(account):
    """The account holds 10 GB and is due for billing. At the default 0.1 credits/GB/day that is
    exactly one credit a day."""

    def _set(gigabytes: int = 10, *, hours_ago: int = 25) -> TodoAccount:
        TodoAccount.objects.filter(pk=account.pk).update(
            stored_bytes=gigabytes * GB, last_billed_at=timezone.now() - timedelta(hours=hours_ago)
        )
        account.refresh_from_db()
        return account

    return _set


def storage_spends(user):
    return CreditTransaction.objects.filter(
        user=user, source='todo', type=CreditTransactionType.SPEND
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
        assert tx.description == 'To-do storage, day 1'

    def test_the_price_is_the_shared_billing_setting(self, stored, funded_user, billing_settings):
        type(billing_settings).objects.filter(pk=1).update(
            storage_price_per_gb_per_day=Decimal('0.5')
        )
        account = stored(10)

        metering.meter_account(account.pk)

        assert get_balance(funded_user) == 95

    def test_a_price_passed_in_is_used_instead_of_loading_it(self, stored, funded_user):
        metering.meter_account(stored(10).pk, Decimal('0.3'))

        assert get_balance(funded_user) == 97

    def test_fractions_are_carried_until_a_whole_credit(self, stored, funded_user):
        account = stored(1)  # 0.1 credit a day

        for day in range(1, 10):
            TodoAccount.objects.filter(pk=account.pk).update(
                last_billed_at=timezone.now() - timedelta(hours=25)
            )
            metering.meter_account(account.pk)
            account.refresh_from_db()
            assert account.accrued == Decimal('0.1') * day
            assert get_balance(funded_user) == 100

        TodoAccount.objects.filter(pk=account.pk).update(
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

    def test_trashed_tasks_attachments_still_count_toward_storage(
        self, funded_user, fake_storage, stored
    ):
        from ..services import trash_task

        task = make_task(funded_user)
        attachment = make_attachment(task, fake_storage)

        trash_task(task)

        assert get_account(funded_user).stored_bytes == attachment.size_bytes

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
        assert mail['subject'] == 'Your to-do file storage is paused'
        assert 'nothing was deleted' in mail['text_body']
        assert '10.0\xa0GB' in mail['text_body'] or '10.0 GB' in mail['text_body']
        assert 'about 1 credit a day' in mail['text_body']
        assert '/billing/' in mail['text_body'] and '/todo/settings/' in mail['text_body']
        assert '<a href="http' in mail['html_body']
        assert 'nothing was deleted' in mail['html_body']

    def test_the_email_is_only_sent_to_a_confirmed_address(
        self, stored, funded_user, sent_emails, django_capture_on_commit_callbacks
    ):
        type(funded_user).objects.filter(pk=funded_user.pk).update(email_confirmed=False)
        spend_credits(funded_user, 100, source='test')

        with django_capture_on_commit_callbacks(execute=True):
            metering.meter_account(stored(10).pk)

        assert sent_emails == []
        assert TodoAccount.objects.get(pk=funded_user.pk).frozen_at is not None

    def test_freezing_notifies_the_owner_in_the_app(self, stored, funded_user):
        spend_credits(funded_user, 100, source='test')
        account = stored(10)

        metering.meter_account(account.pk)

        notification = Notification.objects.get(user=funded_user)
        assert notification.source == 'todo'
        assert notification.title == 'Your to-do file storage is paused'
        assert notification.url == reverse('todo:settings')
        assert 'Nothing was deleted' in notification.body
        assert notification.key == 'todo_account:frozen'

    def test_a_hook_that_fails_midway_leaves_nothing_behind(self, stored, funded_user, monkeypatch):
        def write_then_fail(user, **kwargs):
            Notification.objects.create(user_id=user, source='x', title='half-written')
            raise RuntimeError('boom')

        monkeypatch.setattr('apps.todo.services.metering.notifications.notify', write_then_fail)
        spend_credits(funded_user, 100, source='test')
        account = stored(10)

        metering.meter_account(account.pk)

        account.refresh_from_db()
        assert account.frozen_at is not None
        assert not Notification.objects.exists()

    def test_freezing_twice_notifies_once(self, stored, funded_user):
        spend_credits(funded_user, 100, source='test')
        account = stored(10)

        metering.freeze_account(account)
        metering.freeze_account(TodoAccount.objects.get(pk=account.pk))

        assert Notification.objects.filter(user=funded_user).count() == 1

    def test_freezing_twice_sends_one_email(
        self, stored, funded_user, sent_emails, django_capture_on_commit_callbacks
    ):
        spend_credits(funded_user, 100, source='test')
        account = stored(10)

        with django_capture_on_commit_callbacks(execute=True):
            metering.freeze_account(account)
            metering.freeze_account(TodoAccount.objects.get(pk=account.pk))

        assert len(sent_emails) == 1

    def test_a_paid_day_creates_no_notification_or_email(
        self, stored, funded_user, sent_emails, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            metering.meter_account(stored(10).pk)

        assert not Notification.objects.exists()
        assert sent_emails == []

    def test_the_notification_is_part_of_the_callers_transaction(self, stored, funded_user):
        spend_credits(funded_user, 100, source='test')
        account = stored(10)

        class Abort(Exception):
            pass

        with pytest.raises(Abort), transaction.atomic():
            metering.freeze_account(account)
            raise Abort

        assert not Notification.objects.exists()

    def test_a_failing_notification_does_not_stop_the_freeze(
        self, stored, funded_user, monkeypatch
    ):
        def boom(*args, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr('apps.todo.services.metering.notifications.notify', boom)
        spend_credits(funded_user, 100, source='test')
        account = stored(10)

        metering.meter_account(account.pk)

        account.refresh_from_db()
        assert account.frozen_at is not None

    def test_a_frozen_account_is_not_metered(self, stored, funded_user):
        account = stored(10)
        TodoAccount.objects.filter(pk=account.pk).update(frozen_at=timezone.now())

        metering.meter_account(account.pk)

        assert get_balance(funded_user) == 100


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
        TodoAccount.objects.filter(pk=account.pk).update(
            last_billed_at=timezone.now() - timedelta(days=10)
        )

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 50, source='test')

        account.refresh_from_db()
        assert account.frozen_at is None
        assert timezone.now() - account.last_billed_at < timedelta(minutes=1)
        metering.meter_account(account.pk)
        assert get_balance(funded_user) == 50  # nothing billed for the frozen days

    def test_unfreezing_deletes_the_storage_paused_notice(self, stored, funded_user):
        account = self.freeze(stored, funded_user)
        assert Notification.objects.filter(user=funded_user).count() == 1
        add_credits(funded_user, 50, source='test')

        assert metering.resume(str(funded_user.pk)) is True

        account.refresh_from_db()
        assert account.frozen_at is None
        assert not Notification.objects.filter(user=funded_user).exists()

    def test_unfreezing_leaves_other_notices_and_other_users_alone(
        self, stored, funded_user, other_user
    ):
        self.freeze(stored, funded_user)
        mine = notifications.notify(funded_user, source='todo', title='something else')
        theirs = notifications.notify(
            other_user, source='todo', title='paused', key=metering.FROZEN_NOTICE_KEY
        )
        add_credits(funded_user, 50, source='test')

        metering.resume(str(funded_user.pk))

        assert set(Notification.objects.all()) == {mine, theirs}

    def test_a_balance_that_does_not_unfreeze_keeps_the_notice(self, stored, funded_user):
        self.freeze(stored, funded_user)

        assert metering.resume(str(funded_user.pk)) is False

        assert Notification.objects.filter(user=funded_user).count() == 1

    def test_a_failing_cleanup_never_undoes_the_unfreeze(self, stored, funded_user, monkeypatch):
        account = self.freeze(stored, funded_user)

        def boom(*args, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr('apps.todo.services.metering.notifications.delete_for_key', boom)
        add_credits(funded_user, 50, source='test')

        metering.resume(str(funded_user.pk))

        account.refresh_from_db()
        assert account.frozen_at is None

    def test_no_balance_does_not_unfreeze(self, stored, funded_user):
        account = self.freeze(stored, funded_user)

        assert metering.resume(str(funded_user.pk)) is False

        account.refresh_from_db()
        assert account.frozen_at is not None

    def test_a_balance_of_exactly_one_credit_unfreezes_a_cheap_account(self, stored, funded_user):
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

        assert metering.resume(str(funded_user.pk)) is False

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

    def test_nothing_is_deleted_while_frozen(self, stored, funded_user, fake_storage):
        from ..models import Attachment
        from ..services.storage import attachment_key

        attachment = make_attachment(make_task(funded_user), fake_storage)
        self.freeze(stored, funded_user)

        assert Attachment.objects.filter(pk=attachment.pk).exists()
        assert attachment_key(funded_user.pk, attachment.pk) in fake_storage.objects

    def test_resume_is_idempotent_and_ignores_strangers(self, stored, funded_user, user):
        assert metering.resume('not-a-uuid') is False
        assert metering.resume('00000000-0000-0000-0000-000000000000') is False
        # A user who never used To-do, and one whose account isn't frozen.
        assert metering.resume(str(user.pk)) is False
        stored(10)
        assert metering.resume(str(funded_user.pk)) is False

    def test_the_resume_task_is_on_the_default_queue(self):
        assert metering.resume_account.queue_name == 'default'


class TestMeterStorageJob:
    def test_bills_every_account_with_stored_bytes(self, stored, funded_user, user, other_user):
        stored(10)
        # An empty account due for billing is skipped, and so is one owing nothing yet.
        empty = get_account(other_user)
        TodoAccount.objects.filter(pk=empty.pk).update(
            last_billed_at=timezone.now() - timedelta(hours=30)
        )

        jobs.meter_storage()

        assert get_balance(funded_user) == 99
        assert TodoAccount.objects.get(pk=empty.pk).billed_days == 0

    def test_one_failing_account_does_not_stop_the_rest(
        self, stored, funded_user, other_user, monkeypatch, caplog
    ):
        stored(10)
        add_credits(other_user, 100, source='test')
        theirs = get_account(other_user)
        TodoAccount.objects.filter(pk=theirs.pk).update(
            stored_bytes=10 * GB, last_billed_at=timezone.now() - timedelta(hours=25)
        )
        real = metering.meter_account
        calls = []

        def flaky(account_pk, price=None):
            calls.append(account_pk)
            if account_pk == funded_user.pk:
                raise RuntimeError('boom')
            return real(account_pk, price)

        monkeypatch.setattr(jobs, 'meter_account', flaky)

        jobs.meter_storage()

        assert len(calls) == 2
        assert get_balance(other_user) == 99
        assert 'meter_account failed' in caplog.text

    def test_the_daily_job_resumes_a_frozen_account_that_has_topped_up(self, stored, funded_user):
        account = TestUnfreeze().freeze(stored, funded_user)
        add_credits(funded_user, 5, source='test')  # (no on_commit in a test transaction)

        jobs.meter_storage()

        account.refresh_from_db()
        assert account.frozen_at is None

    def test_an_account_frozen_by_this_very_run_is_not_resumed_by_it(
        self, stored, funded_user, monkeypatch
    ):
        # The balance would cover a day, so a resume offered in the same run would undo the freeze
        # at once (and forgive the day): the charge is made to fail anyway, as in a race.
        def refuse(*args, **kwargs):
            raise InsufficientCreditsError('race')

        monkeypatch.setattr(metering, 'spend_credits', refuse)
        account = stored(10)

        jobs.meter_storage()

        account.refresh_from_db()
        assert account.frozen_at is not None
        assert get_balance(funded_user) == 100

    def test_a_frozen_account_that_cannot_pay_stays_frozen(self, stored, funded_user):
        account = TestUnfreeze().freeze(stored, funded_user)

        jobs.meter_storage()

        account.refresh_from_db()
        assert account.frozen_at is not None


def test_the_jobs_are_registered_with_the_scheduler():
    # `ready()` registers them at startup; the core scheduler tests clear the global registry, so
    # re-run that step (registering a name again just replaces it) to test it whatever ran before.
    apps.get_app_config('todo')._register_jobs()
    specs = {job.name: job for job in get_jobs() if job.name.startswith('todo.')}

    assert {'todo.meter_storage', 'todo.cleanup_uploads', 'todo.purge_trash'} <= set(specs)
    assert specs['todo.meter_storage'].interval == timedelta(hours=24)
    assert specs['todo.meter_storage'].func is jobs.meter_storage
    assert specs['todo.cleanup_uploads'].interval == timedelta(hours=1)
    assert specs['todo.cleanup_uploads'].func is jobs.cleanup_uploads


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
    def test_a_top_up_for_a_user_without_a_frozen_account_queues_nothing(
        self, user, funded_user, monkeypatch, django_capture_on_commit_callbacks
    ):
        calls = []
        monkeypatch.setattr(metering, 'resume_account', FakeTask(calls))
        get_account(funded_user)  # has an account, but it is not frozen

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 5, source='test')  # no To-do account at all
            add_credits(funded_user, 5, source='test')

        assert calls == []

    def test_a_top_up_queues_resume_account_with_the_plain_user_id(
        self, funded_user, account, monkeypatch, django_capture_on_commit_callbacks
    ):
        calls = []
        monkeypatch.setattr(metering, 'resume_account', FakeTask(calls))
        TodoAccount.objects.filter(pk=account.pk).update(frozen_at=timezone.now())

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 5, source='test')

        assert calls == [(str(funded_user.pk),)]

    def test_a_failing_queue_never_stops_the_top_up_or_the_settling(
        self,
        funded_user,
        account,
        monkeypatch,
        django_capture_on_commit_callbacks,
        caplog,
    ):
        def boom():
            raise RuntimeError('queue down')

        monkeypatch.setattr(metering, 'resume_account', FakeTask([], boom))
        TodoAccount.objects.filter(pk=account.pk).update(frozen_at=timezone.now())

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 5, source='test')  # must not raise

        assert get_balance(funded_user) == 105
        assert 'Could not queue' in caplog.text

    def test_settling_unpaid_tasks_is_still_queued_next_to_it(
        self, funded_user, account, monkeypatch, django_capture_on_commit_callbacks
    ):
        from ..services import billing

        settled = []
        monkeypatch.setattr(billing, 'settle_unpaid_tasks', FakeTask(settled))
        monkeypatch.setattr(metering, 'resume_account', FakeTask([], lambda: 1 / 0))
        TodoAccount.objects.filter(pk=account.pk).update(
            frozen_at=timezone.now(), creation_blocked_at=timezone.now()
        )

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(funded_user, 5, source='test')

        assert settled == [(str(funded_user.pk),)]


def test_add_stored_bytes_restarts_the_clock_for_an_empty_account(account, funded_user):
    TodoAccount.objects.filter(pk=account.pk).update(
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
    TodoAccount.objects.filter(pk=account.pk).update(last_billed_at=earlier)

    add_stored_bytes(funded_user.pk, 500)

    account.refresh_from_db()
    assert account.stored_bytes == 1500
    assert account.last_billed_at == earlier


def test_add_stored_bytes_never_goes_below_zero(account, funded_user):
    add_stored_bytes(funded_user.pk, 100)

    add_stored_bytes(funded_user.pk, -500)

    account.refresh_from_db()
    assert account.stored_bytes == 0
