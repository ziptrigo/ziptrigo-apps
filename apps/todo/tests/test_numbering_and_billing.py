"""Task numbers (per user, from 1000, never reused) and what creating a task costs."""

from datetime import date, time

import pytest
from django.db.models import QuerySet
from django.utils import timezone as dj_timezone

from apps.billing.services import InsufficientCreditsError, add_credits, get_balance, spend_credits

from ..models import Task, TodoAccount
from ..services import (
    FIRST_TASK_NUMBER,
    allocate_number,
    charge_task_creation,
    create_task,
    get_account,
    month_usage,
    trash_task,
)
from ..services.billing import month_key, settle
from .conftest import balance
from .factories import make_user

pytestmark = pytest.mark.django_db


class TestNumbering:
    def test_numbers_start_at_1000_and_count_up(self, user, config):
        first = create_task(user, title='One')
        second = create_task(user, title='Two')

        assert (first.number, second.number) == (FIRST_TASK_NUMBER, FIRST_TASK_NUMBER + 1)
        assert FIRST_TASK_NUMBER == 1000

    def test_numbers_are_per_user(self, user, other_user, config):
        create_task(user, title='Mine')

        assert create_task(other_user, title='Theirs').number == 1000

    def test_a_trashed_or_purged_task_keeps_its_number_out_of_circulation(self, user, config):
        first = create_task(user, title='One')
        trash_task(first)
        first.delete()

        assert create_task(user, title='Two').number == 1001

    def test_a_refused_creation_gives_the_number_back(self, user, config):
        """The whole creation is one transaction: a refusal rolls the allocation back too."""
        with pytest.raises(InsufficientCreditsError):
            for _ in range(5):
                create_task(user, title='x')  # unfunded: the third costs 5

        assert TodoAccount.objects.get(user=user).next_task_number == 1002
        assert Task.objects.filter(user=user).count() == 2

    def test_allocation_locks_the_account_row(self, user, monkeypatch):
        """SQLite has no row locks and so cannot show the race; this records that the account row
        is asked for `FOR UPDATE` before its number is read (like Receipts' `TestLockOrder`)."""
        locked: list[str] = []
        real = QuerySet.select_for_update

        def recording(self, *args, **kwargs):
            locked.append(self.model.__name__)
            return real(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, 'select_for_update', recording)
        account = get_account(user)

        assert allocate_number(account) == 1000
        assert allocate_number(account) == 1001
        assert locked == ['TodoAccount', 'TodoAccount']

    def test_create_task_allocates_under_the_lock(self, user, config, monkeypatch):
        locked: list[str] = []
        real = QuerySet.select_for_update

        def recording(self, *args, **kwargs):
            locked.append(self.model.__name__)
            return real(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, 'select_for_update', recording)

        create_task(user, title='One')

        assert locked == ['TodoAccount']

    def test_get_account_creates_it_once_with_the_defaults(self, user):
        account = get_account(user)

        assert get_account(user).pk == account.pk
        assert account.next_task_number == 1000
        assert account.default_due_time == time(9, 0)
        assert account.creation_blocked_at is None


class TestFreeAllowanceAndPrice:
    def test_the_first_tasks_of_the_month_are_free(self, user, config):
        create_task(user, title='One')
        create_task(user, title='Two')

        assert balance(user) == 0
        assert list(Task.objects.values_list('charged_credits', flat=True)) == [0, 0]

    def test_a_task_beyond_the_allowance_costs_the_price(self, funded_user):
        for title in ('One', 'Two', 'Three'):
            task = create_task(funded_user, title=title)

        assert task.charged_credits == 5
        assert balance(funded_user) == 95
        transaction = funded_user.credit_transactions.latest('created_at')
        assert (transaction.amount, transaction.source) == (-5, 'todo')
        assert transaction.description == 'To-do: task #1002'

    def test_a_subtask_counts_too(self, funded_user):
        parent = create_task(funded_user, title='Parent')
        create_task(funded_user, title='Sub', parent=parent)
        sub = create_task(funded_user, title='Sub 2', parent=parent)

        assert sub.charged_credits == 5

    def test_a_price_of_zero_makes_tasks_free(self, funded_user, config):
        config.task_price = 0
        config.save()

        for title in ('One', 'Two', 'Three', 'Four'):
            create_task(funded_user, title=title)

        assert balance(funded_user) == 100

    def test_without_credits_a_task_beyond_the_allowance_is_refused(self, user, config):
        create_task(user, title='One')
        create_task(user, title='Two')

        with pytest.raises(InsufficientCreditsError):
            create_task(user, title='Three')

        assert Task.objects.filter(user=user).count() == 2
        account = TodoAccount.objects.get(user=user)
        assert account.month_task_count == 2  # the refused one is not counted
        assert account.creation_blocked_at is None

    def test_month_usage(self, funded_user, config):
        create_task(funded_user, title='One')

        assert month_usage(get_account(funded_user), config) == (1, 2)

    def test_the_count_resets_in_a_new_month(self, funded_user, config, freeze_now):
        freeze_now(2026, 10, 30)
        for title in ('One', 'Two'):
            create_task(funded_user, title=title)
        freeze_now(2026, 11, 2)

        assert month_usage(get_account(funded_user), config) == (0, 2)
        task = create_task(funded_user, title='Three')

        assert task.charged_credits == 0
        account = TodoAccount.objects.get(user=funded_user)
        assert (account.month_key, account.month_task_count) == ('2026-11', 1)

    def test_the_month_is_the_users_own(self, funded_user, config, freeze_now):
        """At 23:30 UTC on 31 October it is already 1 November in Auckland."""
        funded_user.timezone = 'Pacific/Auckland'
        funded_user.save()
        freeze_now(2026, 10, 31, 23, 30)

        assert month_key(funded_user) == '2026-11'
        create_task(funded_user, title='One')
        assert TodoAccount.objects.get(user=funded_user).month_key == '2026-11'

        funded_user.timezone = ''
        funded_user.save()
        assert month_key(funded_user) == '2026-10'

    def test_a_month_boundary_in_the_users_zone_resets_the_count(
        self, funded_user, config, freeze_now
    ):
        funded_user.timezone = 'America/Los_Angeles'
        funded_user.save()
        freeze_now(2026, 11, 1, 5, 0)  # still 31 October in Los Angeles
        create_task(funded_user, title='One')
        create_task(funded_user, title='Two')
        assert TodoAccount.objects.get(user=funded_user).month_key == '2026-10'

        freeze_now(2026, 11, 1, 9, 0)  # 1 November there
        task = create_task(funded_user, title='Three')

        assert task.charged_credits == 0
        assert TodoAccount.objects.get(user=funded_user).month_task_count == 1

    def test_charge_task_creation_returns_credits_and_unpaid(self, user, config):
        account = get_account(user)
        account.month_key = month_key(user)
        account.month_task_count = 2
        account.save()

        assert charge_task_creation(account, config, number=1000, allow_unpaid=True) == (5, True)
        assert account.creation_blocked_at is not None


class TestUnpaidTasks:
    def test_allow_unpaid_creates_the_task_owed_and_blocks_creation(self, user, config):
        create_task(user, title='One')
        create_task(user, title='Two')

        owed = create_task(user, title='Occurrence', allow_unpaid=True, source='recurrence')

        assert owed.unpaid_at is not None
        assert owed.charged_credits == 5
        assert balance(user) == 0
        assert TodoAccount.objects.get(user=user).creation_blocked_at is not None

    def test_a_blocked_account_refuses_new_tasks(self, user, config):
        for title in ('One', 'Two', 'Owed'):
            create_task(user, title=title, allow_unpaid=True)

        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as caught:
            create_task(user, title='Refused')

        assert 'Top up your credits to add tasks' in caught.value.messages[0]
        assert Task.objects.filter(user=user).count() == 3

    def test_a_blocked_account_still_accepts_an_unpaid_occurrence(self, user, config):
        for title in ('One', 'Two', 'Owed', 'Owed 2'):
            create_task(user, title=title, allow_unpaid=True)

        assert Task.objects.filter(user=user, unpaid_at__isnull=False).count() == 2

    def test_a_top_up_settles_oldest_first_and_unblocks(
        self, user, config, django_capture_on_commit_callbacks
    ):
        for title in ('One', 'Two', 'Owed 1', 'Owed 2'):
            create_task(user, title=title, allow_unpaid=True)
        assert TodoAccount.objects.get(user=user).creation_blocked_at is not None

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 12, description='Top-up', source='test')

        assert not Task.objects.filter(user=user, unpaid_at__isnull=False).exists()
        assert balance(user) == 2
        assert TodoAccount.objects.get(user=user).creation_blocked_at is None
        descriptions = list(
            user.credit_transactions.filter(amount__lt=0).values_list('description', flat=True)
        )
        assert sorted(descriptions) == ['To-do: task #1002', 'To-do: task #1003']

    def test_a_partial_top_up_settles_what_it_covers_and_stays_blocked(
        self, user, config, django_capture_on_commit_callbacks
    ):
        for title in ('One', 'Two', 'Owed 1', 'Owed 2'):
            create_task(user, title=title, allow_unpaid=True)

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 7, description='Top-up', source='test')

        first, second = Task.objects.filter(user=user, number__in=(1002, 1003)).order_by('number')
        assert first.unpaid_at is None  # the oldest went first
        assert second.unpaid_at is not None
        assert balance(user) == 2
        assert TodoAccount.objects.get(user=user).creation_blocked_at is not None

    def test_settling_stops_at_the_first_it_cannot_cover(self, user, config):
        for title in ('One', 'Two', 'Owed 1', 'Owed 2', 'Owed 3'):
            create_task(user, title=title, allow_unpaid=True)
        add_credits(user, 5, description='Top-up', source='test')

        assert settle(str(user.pk)) == 1
        assert Task.objects.filter(user=user, unpaid_at__isnull=False).count() == 2

    def test_settling_is_idempotent(self, user, config):
        for title in ('One', 'Two', 'Owed'):
            create_task(user, title=title, allow_unpaid=True)
        add_credits(user, 50, description='Top-up', source='test')

        assert settle(str(user.pk)) == 1
        assert settle(str(user.pk)) == 0
        assert get_balance(user) == 45

    def test_the_task_is_a_noop_for_an_unknown_user(self, db):
        assert settle('00000000-0000-0000-0000-000000000000') == 0
        assert settle('not-a-uuid') == 0

    def test_a_top_up_for_a_user_who_owes_nothing_queues_nothing(
        self, user, config, django_capture_on_commit_callbacks, monkeypatch
    ):
        queued = []
        monkeypatch.setattr(
            'apps.todo.services.billing.enqueue_after_commit', lambda *a: queued.append(a)
        )
        create_task(user, title='One')

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 10, description='Top-up', source='test')

        assert queued == []

    def test_a_failing_receiver_never_stops_the_signal(self, user, monkeypatch):
        """`credits_added` is sent with `send`: an exception here would skip the other receivers."""
        from apps.billing.signals import credits_added

        def boom(user):
            raise RuntimeError('queue down')

        monkeypatch.setattr('apps.todo.services.billing.on_credits_added', boom)

        credits_added.send(sender=None, user=user, amount=1)  # does not raise

    def test_an_unpaid_task_keeps_its_price_after_being_trashed(self, user, config):
        for title in ('One', 'Two'):
            create_task(user, title=title)
        owed = create_task(user, title='Owed', allow_unpaid=True)
        trash_task(owed)
        add_credits(user, 5, description='Top-up', source='test')

        settle(str(user.pk))

        owed.refresh_from_db()
        assert owed.unpaid_at is None
        assert balance(user) == 0


def test_spend_helper_sanity(user):
    """The ledger refuses to go below zero (the contract the unpaid flow relies on)."""
    with pytest.raises(InsufficientCreditsError):
        spend_credits(user, 1)


def test_other_users_are_unaffected_by_a_block(user, config):
    for title in ('One', 'Two', 'Owed'):
        create_task(user, title=title, allow_unpaid=True)
    stranger = make_user()

    assert create_task(stranger, title='Free').charged_credits == 0


def test_month_key_matches_the_users_today(user, freeze_now):
    freeze_now(2026, 3, 1, 0, 30)

    assert month_key(user) == '2026-03'
    assert dj_timezone.now().date() == date(2026, 3, 1)
