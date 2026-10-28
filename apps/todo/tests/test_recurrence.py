"""Repeating tasks (issue #179): the rule subset, its description, the next date, what completing an
occurrence creates and bills, stopping, the form and the pages."""

from datetime import date, time

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.billing.services import add_credits
from apps.core.models import Notification

from ..models import Recurrence, RecurrenceMode, Task, TodoAccount, TodoSettings
from ..services import (
    complete_task,
    create_task,
    get_account,
    month_usage,
    postpone_task,
    recurrence,
    trash_task,
    uncomplete_task,
    update_task,
)
from ..services.recurrence import (
    describe,
    failure_key,
    first_date,
    next_date,
    on_completed,
    parse_rule,
)
from .conftest import balance
from .factories import make_label, make_task, make_user

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
MONTHLY_FIRST = 'FREQ=MONTHLY;BYMONTHDAY=1'
TODAY = date(2026, 10, 28)


def occurrences(user) -> list[Task]:
    return list(Task.objects.filter(user=user, parent__isnull=True).order_by('number'))


class TestParseRule:
    @pytest.mark.parametrize(
        ('value', 'canonical'),
        [
            ('FREQ=DAILY', 'FREQ=DAILY'),
            ('freq=daily', 'FREQ=DAILY'),
            ('RRULE:FREQ=DAILY;INTERVAL=1', 'FREQ=DAILY'),
            ('FREQ=DAILY;INTERVAL=365', 'FREQ=DAILY;INTERVAL=365'),
            ('FREQ=WEEKLY;BYDAY=WE,MO;WKST=SU', 'FREQ=WEEKLY;BYDAY=MO,WE'),
            ('FREQ=MONTHLY;BYMONTHDAY=1', 'FREQ=MONTHLY;BYMONTHDAY=1'),
            ('FREQ=MONTHLY;BYMONTHDAY=-1', 'FREQ=MONTHLY;BYMONTHDAY=-1'),
            ('FREQ=MONTHLY;BYDAY=1MO', 'FREQ=MONTHLY;BYDAY=1MO'),
            ('FREQ=MONTHLY;BYDAY=-1FR', 'FREQ=MONTHLY;BYDAY=-1FR'),
            ('FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=3', 'FREQ=YEARLY;BYMONTHDAY=3;BYMONTH=3'),
            ('FREQ=DAILY;COUNT=1000', 'FREQ=DAILY;COUNT=1000'),
            ('FREQ=DAILY;UNTIL=20991231', 'FREQ=DAILY;UNTIL=20991231'),
            ('  FREQ=MONTHLY;INTERVAL=3  ', 'FREQ=MONTHLY;INTERVAL=3'),
        ],
    )
    def test_accepts_and_canonicalises(self, value, canonical):
        assert parse_rule(value, TODAY) == canonical
        assert parse_rule(canonical, TODAY) == canonical  # idempotent

    @pytest.mark.parametrize(
        'value',
        [
            '',
            'x',
            'INTERVAL=2',
            'FREQ=HOURLY',
            'FREQ=SECONDLY',
            'FREQ=DAILY;BYHOUR=9',
            'FREQ=MONTHLY;BYSETPOS=1;BYDAY=MO',
            'FREQ=YEARLY;BYWEEKNO=3',
            'FREQ=YEARLY;BYYEARDAY=100',
            'FREQ=DAILY;INTERVAL=0',
            'FREQ=DAILY;INTERVAL=366',
            'FREQ=DAILY;INTERVAL=2;INTERVAL=3',
            'FREQ=WEEKLY;BYDAY=1MO',  # an ordinal is for MONTHLY only
            'FREQ=MONTHLY;BYDAY=5MO',
            'FREQ=MONTHLY;BYDAY=0MO',
            'FREQ=MONTHLY;BYDAY=XX',
            'FREQ=MONTHLY;BYMONTHDAY=0',
            'FREQ=MONTHLY;BYMONTHDAY=32',
            'FREQ=MONTHLY;BYMONTHDAY=-2',
            'FREQ=YEARLY;BYMONTH=13',
            'FREQ=DAILY;COUNT=0',
            'FREQ=DAILY;COUNT=1001',
            'FREQ=DAILY;COUNT=2;UNTIL=20991231',
            'FREQ=DAILY;UNTIL=2099-12-31',
            'FREQ=DAILY;UNTIL=20991331',
            'FREQ=DAILY;UNTIL=20991231T000000Z',
            'FREQ=DAILY;FOO=1',
        ],
    )
    def test_refuses_what_is_not_supported(self, value):
        with pytest.raises(ValidationError) as caught:
            parse_rule(value, TODAY)
        assert caught.value.messages == ['Unsupported repeat rule.']

    def test_refuses_a_rule_longer_than_255_characters(self):
        with pytest.raises(ValidationError, match='too long'):
            parse_rule('FREQ=WEEKLY;BYDAY=' + ','.join(['MO'] * 100), TODAY)

    @pytest.mark.parametrize(
        'value', ['FREQ=DAILY;UNTIL=20200101', 'FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30']
    )
    def test_refuses_a_rule_that_never_occurs_again(self, value):
        with pytest.raises(ValidationError, match='no upcoming date'):
            parse_rule(value, TODAY)


class TestParseRuleToday:
    def test_until_the_users_local_today_is_accepted_in_the_evening_in_chicago(
        self, funded_user, freeze_now
    ):
        funded_user.timezone = 'America/Chicago'
        funded_user.save()
        freeze_now(2026, 10, 29, 4, 30)  # 23:30 on the 28th in Chicago, the 29th in UTC

        task = create_task(
            funded_user,
            title='Last one',
            due_date=date(2026, 10, 28),
            rrule='FREQ=DAILY;UNTIL=20261028',
        )

        assert task.recurrence is not None
        assert parse_rule('FREQ=DAILY;UNTIL=20261028', date(2026, 10, 28)) == (
            'FREQ=DAILY;UNTIL=20261028'
        )
        with pytest.raises(ValidationError, match='no upcoming date'):
            parse_rule('FREQ=DAILY;UNTIL=20261028', date(2026, 10, 29))  # UTC's today: past it

    def test_the_form_checks_against_the_users_today(self, web, funded_user, freeze_now):
        funded_user.timezone = 'America/Chicago'
        funded_user.save()
        freeze_now(2026, 10, 29, 4, 30)

        response = web.post(
            reverse('todo:create'),
            {'title': 'Last', 'due_date': '2026-10-28', 'rrule': 'FREQ=DAILY;UNTIL=20261028'},
            **HTMX,
        )

        assert response.status_code != 422
        assert Task.objects.filter(title='Last').exists()


class TestDescribe:
    @pytest.mark.parametrize(
        ('rule', 'text'),
        [
            ('FREQ=DAILY', 'Every day'),
            ('FREQ=DAILY;INTERVAL=3', 'Every 3 days'),
            ('FREQ=WEEKLY', 'Every week'),
            ('FREQ=WEEKLY;BYDAY=MO', 'Every week on Monday'),
            ('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE', 'Every 2 weeks on Monday, Wednesday'),
            ('FREQ=MONTHLY;BYMONTHDAY=1', 'Every month on the 1st'),
            ('FREQ=MONTHLY;BYMONTHDAY=2', 'Every month on the 2nd'),
            ('FREQ=MONTHLY;BYMONTHDAY=3', 'Every month on the 3rd'),
            ('FREQ=MONTHLY;BYMONTHDAY=11', 'Every month on the 11th'),
            ('FREQ=MONTHLY;BYMONTHDAY=22', 'Every month on the 22nd'),
            ('FREQ=MONTHLY;BYMONTHDAY=-1', 'Every month on the last day'),
            ('FREQ=MONTHLY;BYDAY=1MO', 'Every month on the first Monday'),
            ('FREQ=MONTHLY;BYDAY=-1FR', 'Every month on the last Friday'),
            ('FREQ=MONTHLY;INTERVAL=3', 'Every 3 months'),
            ('FREQ=YEARLY;BYMONTHDAY=3;BYMONTH=3', 'Every year on 3 March'),
            ('FREQ=YEARLY', 'Every year'),
            ('FREQ=DAILY;COUNT=5', 'Every day, 5 times'),
            ('FREQ=DAILY;COUNT=1', 'Every day, 1 time'),
            (
                'FREQ=MONTHLY;BYMONTHDAY=1;UNTIL=20261231',
                'Every month on the 1st, until 31 Dec 2026',
            ),
        ],
    )
    def test_sentences(self, rule, text):
        assert describe(rule) == text

    @pytest.mark.parametrize(
        'rule',
        [
            'FREQ=DAILY;BYDAY=MO',
            'FREQ=MONTHLY;BYMONTHDAY=1,15',
            'FREQ=MONTHLY;BYDAY=MO',
            'FREQ=MONTHLY;BYDAY=1MO;BYMONTHDAY=1',
            'FREQ=YEARLY;BYMONTH=3',
            'FREQ=WEEKLY;BYMONTHDAY=1',
            'FREQ=DAILY;BYHOUR=3',
            'nonsense',
        ],
    )
    def test_anything_else_is_custom(self, rule):
        assert describe(rule) == f'Custom repeat ({rule})'


class TestNextDate:
    def test_monthly_on_the_31st_skips_the_months_without_one(self):
        rule = 'FREQ=MONTHLY;BYMONTHDAY=31'

        assert next_date(rule, date(2026, 1, 31)) == date(2026, 3, 31)  # not February
        assert next_date(rule, date(2026, 3, 31)) == date(2026, 5, 31)  # not April
        assert next_date(rule, date(2026, 1, 1)) == date(2026, 1, 31)

    def test_minus_one_is_the_last_day_of_each_month(self):
        rule = 'FREQ=MONTHLY;BYMONTHDAY=-1'

        assert next_date(rule, date(2026, 1, 31)) == date(2026, 2, 28)
        assert next_date(rule, date(2028, 1, 31)) == date(2028, 2, 29)  # a leap year
        assert next_date(rule, date(2026, 2, 28)) == date(2026, 3, 31)

    def test_a_day_less_monthly_rule_is_clamped_to_short_months_and_keeps_its_day(self):
        rule = 'FREQ=MONTHLY'

        assert next_date(rule, date(2026, 1, 31)) == date(2026, 2, 28)
        assert next_date(rule, date(2026, 2, 28), day=31) == date(2026, 3, 31)
        assert next_date(rule, date(2026, 3, 31)) == date(2026, 4, 30)
        assert next_date(rule, date(2026, 4, 30), day=31) == date(2026, 5, 31)
        assert next_date(rule, date(2028, 1, 31)) == date(2028, 2, 29)  # a leap year
        assert next_date(rule, date(2026, 1, 30)) == date(2026, 2, 28)
        assert next_date(rule, date(2026, 1, 29)) == date(2026, 2, 28)
        assert next_date(rule, date(2026, 1, 28)) == date(2026, 2, 28)

    def test_the_clamp_respects_the_interval_and_until(self):
        assert next_date('FREQ=MONTHLY;INTERVAL=2', date(2026, 1, 31)) == date(2026, 3, 31)
        assert next_date('FREQ=MONTHLY;INTERVAL=3', date(2026, 11, 30), day=31) == date(2027, 2, 28)
        # The 1st of February is before the end date, but the clamped 28th is not.
        assert next_date('FREQ=MONTHLY;UNTIL=20260215', date(2026, 1, 31)) is None
        assert next_date('FREQ=MONTHLY;UNTIL=20260228', date(2026, 1, 31)) == date(2026, 2, 28)

    def test_a_day_less_yearly_rule_on_the_29th_of_february(self):
        rule = 'FREQ=YEARLY'

        assert next_date(rule, date(2028, 2, 29)) == date(2029, 2, 28)
        assert next_date(rule, date(2029, 2, 28), day=29) == date(2030, 2, 28)
        assert next_date(rule, date(2031, 2, 28), day=29) == date(2032, 2, 29)
        assert next_date('FREQ=YEARLY;INTERVAL=4', date(2028, 2, 29)) == date(2032, 2, 29)
        assert next_date('FREQ=YEARLY;BYMONTH=2', date(2026, 10, 31)) == date(2027, 2, 28)

    def test_first_date_includes_today_and_clamps(self):
        assert first_date('FREQ=MONTHLY', date(2026, 1, 31)) == date(2026, 1, 31)
        assert first_date('FREQ=YEARLY', date(2028, 2, 29)) == date(2028, 2, 29)
        assert first_date('FREQ=YEARLY;BYMONTH=2', date(2026, 10, 31)) == date(2027, 2, 28)

    def test_last_friday(self):
        assert next_date('FREQ=MONTHLY;BYDAY=-1FR', date(2026, 10, 28)) == date(2026, 10, 30)
        assert next_date('FREQ=MONTHLY;BYDAY=-1FR', date(2026, 10, 30)) == date(2026, 11, 27)

    def test_first_monday(self):
        assert next_date('FREQ=MONTHLY;BYDAY=1MO', date(2026, 10, 28)) == date(2026, 11, 2)

    def test_yearly(self):
        rule = 'FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=3'

        assert next_date(rule, date(2026, 3, 3)) == date(2027, 3, 3)
        assert next_date(rule, date(2026, 10, 28)) == date(2027, 3, 3)
        assert next_date('FREQ=YEARLY;INTERVAL=2', date(2026, 5, 4)) == date(2028, 5, 4)

    def test_weekly_with_an_interval(self):
        rule = 'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO'

        assert next_date(rule, date(2026, 10, 26)) == date(2026, 11, 9)

    def test_every_three_months_counts_from_the_date_it_is_given(self):
        assert next_date('FREQ=MONTHLY;INTERVAL=3', date(2026, 3, 8)) == date(2026, 6, 8)

    def test_until_is_inclusive_and_then_exhausted(self):
        rule = 'FREQ=DAILY;UNTIL=20261030'

        assert next_date(rule, date(2026, 10, 29)) == date(2026, 10, 30)
        assert next_date(rule, date(2026, 10, 30)) is None

    def test_count_is_not_the_rules_to_apply(self):
        # Tracked by `occurrences_created`: a count restarting at every date would never end.
        assert next_date('FREQ=DAILY;COUNT=1', date(2026, 1, 1)) == date(2026, 1, 2)


class TestCompletionFixedMode:
    def test_the_next_one_is_computed_from_the_schedule_not_from_when_it_was_paid(
        self, funded_user, freeze_now
    ):
        freeze_now(2026, 10, 28)
        rent = create_task(
            funded_user, title='Pay rent', due_date=date(2026, 11, 1), rrule=MONTHLY_FIRST
        )
        assert Recurrence.objects.get().anchor_date == date(2026, 11, 1)
        freeze_now(2026, 11, 3)
        postpone_task(rent, preset='date', date=date(2026, 11, 5))
        freeze_now(2026, 11, 7)

        complete_task(rent)

        _, december = occurrences(funded_user)
        assert december.due_date == date(2026, 12, 1)
        assert december.title == 'Pay rent'
        assert december.source == 'recurrence'
        assert december.recurrence_id == rent.recurrence_id
        state = Recurrence.objects.get()
        assert (state.anchor_date, state.occurrences_created) == (date(2026, 12, 1), 2)

    def test_postponing_never_moves_the_anchor(self, funded_user, freeze_now):
        freeze_now(2026, 10, 28)
        rent = create_task(
            funded_user, title='Pay rent', due_date=date(2026, 11, 1), rrule=MONTHLY_FIRST
        )

        postpone_task(rent, preset='tomorrow')
        update_task(rent, changes={'due_date': date(2027, 1, 20)})

        assert Recurrence.objects.get().anchor_date == date(2026, 11, 1)

    def test_a_late_completion_can_create_an_overdue_next_occurrence(self, funded_user, freeze_now):
        freeze_now(2026, 10, 28)
        daily = create_task(
            funded_user, title='Water', due_date=date(2026, 10, 20), rrule='FREQ=DAILY'
        )

        complete_task(daily)

        assert occurrences(funded_user)[1].due_date == date(2026, 10, 21)

    def test_time_labels_priority_and_description_are_copied(self, funded_user, wednesday):
        label = make_label(funded_user, 'Home')
        original = create_task(
            funded_user,
            title='Standup',
            description='**Notes**',
            priority=2,
            due_date=wednesday,
            due_time=time(9, 30),
            labels=[label],
            rrule='FREQ=DAILY',
        )

        complete_task(original)

        copy = occurrences(funded_user)[1]
        assert (copy.description, copy.priority, copy.due_time) == ('**Notes**', 2, time(9, 30))
        assert list(copy.labels.all()) == [label]
        assert copy.due_date == date(2026, 10, 29)
        assert copy.completed_at is None

    def test_completing_a_second_time_does_nothing(self, funded_user, wednesday):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(task)

        complete_task(task)

        assert len(occurrences(funded_user)) == 2


class TestCompletionFromCompletionMode:
    def test_the_next_one_is_counted_from_the_day_it_was_completed(self, funded_user, freeze_now):
        freeze_now(2026, 3, 1)
        tires = create_task(
            funded_user,
            title='Rotate tires',
            due_date=date(2026, 3, 1),
            rrule='FREQ=MONTHLY;INTERVAL=3',
            recurrence_mode=RecurrenceMode.FROM_COMPLETION,
        )
        freeze_now(2026, 3, 8)

        complete_task(tires)

        assert occurrences(funded_user)[1].due_date == date(2026, 6, 8)

    def test_completing_early_moves_the_next_one_earlier(self, funded_user, freeze_now):
        freeze_now(2026, 3, 1)
        tires = create_task(
            funded_user,
            title='Rotate tires',
            due_date=date(2026, 3, 20),
            rrule='FREQ=MONTHLY;INTERVAL=3',
            recurrence_mode=RecurrenceMode.FROM_COMPLETION,
        )

        complete_task(tires)

        assert occurrences(funded_user)[1].due_date == date(2026, 6, 1)

    def test_the_completion_date_is_the_users_local_one(self, funded_user, freeze_now):
        funded_user.timezone = 'America/Chicago'
        funded_user.save()
        freeze_now(2026, 10, 28, 12)
        task = create_task(
            funded_user,
            title='Weekly',
            due_date=date(2026, 10, 28),
            rrule='FREQ=WEEKLY',
            recurrence_mode=RecurrenceMode.FROM_COMPLETION,
        )
        freeze_now(2026, 10, 29, 4, 30)  # 23:30 on the 28th in Chicago, the 29th in UTC

        complete_task(task)

        assert occurrences(funded_user)[1].due_date == date(2026, 11, 4)  # not the 5th


class TestEndOfTheRule:
    def test_count_ends_the_repeat_after_that_many_occurrences(self, funded_user, wednesday):
        task = create_task(
            funded_user, title='Twice', due_date=wednesday, rrule='FREQ=DAILY;COUNT=2'
        )

        complete_task(task)
        second = occurrences(funded_user)[1]
        assert Recurrence.objects.get().ended_at is None
        complete_task(second)

        assert len(occurrences(funded_user)) == 2
        state = Recurrence.objects.get()
        assert state.ended_at is not None and state.occurrences_created == 2
        assert second.active_recurrence is None

    def test_until_ends_the_repeat_when_no_date_is_left(self, funded_user, wednesday):
        task = create_task(
            funded_user, title='Short', due_date=wednesday, rrule='FREQ=DAILY;UNTIL=20261029'
        )
        complete_task(task)
        second = occurrences(funded_user)[1]
        assert second.due_date == date(2026, 10, 29)

        complete_task(second)

        assert len(occurrences(funded_user)) == 2
        assert Recurrence.objects.get().ended_at is not None

    def test_a_stopped_repeat_creates_nothing(self, funded_user, wednesday):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')

        assert recurrence.stop(task) is True
        complete_task(task)

        assert len(occurrences(funded_user)) == 1
        assert Task.objects.get(pk=task.pk).completed_at is not None  # the task itself is kept


class TestSubtasksAndBilling:
    def test_open_subtasks_are_copied_and_completed_ones_are_not(self, funded_user, wednesday):
        label = make_label(funded_user, 'Work')
        parent = create_task(funded_user, title='Clean', due_date=wednesday, rrule='FREQ=DAILY')
        create_task(funded_user, title='Kitchen', priority=1, labels=[label], parent=parent)
        create_task(funded_user, title='Bathroom', description='deep', parent=parent)
        done = create_task(funded_user, title='Hall', parent=parent)
        complete_task(done)

        complete_task(parent)

        new_parent = Task.objects.get(user=funded_user, parent__isnull=True, completed_at=None)
        titles = list(new_parent.subtasks.order_by('number').values_list('title', flat=True))
        assert titles == ['Kitchen', 'Bathroom']
        kitchen = new_parent.subtasks.get(title='Kitchen')
        assert kitchen.priority == 1 and list(kitchen.labels.all()) == [label]
        assert kitchen.due_date is None and kitchen.source == 'recurrence'
        assert new_parent.subtasks.get(title='Bathroom').description == 'deep'
        # the completed parent keeps what it had, now all done
        assert parent.subtasks.filter(completed_at__isnull=True).count() == 0

    def test_the_copies_keep_a_manual_order(self, funded_user, wednesday):
        from ..services import reorder

        parent = create_task(funded_user, title='Clean', due_date=wednesday, rrule='FREQ=DAILY')
        a, b, c = (create_task(funded_user, title=t, parent=parent) for t in ('A', 'B', 'C'))
        reorder(funded_user, scope='subtasks', ids=[c.pk, a.pk, b.pk], parent=parent)

        complete_task(parent)

        new_parent = Task.objects.get(user=funded_user, parent__isnull=True, completed_at=None)
        ordered = new_parent.subtasks.order_by('position').values_list('title', flat=True)
        assert list(ordered) == ['C', 'A', 'B']

    def test_each_row_created_counts_and_is_charged(self, funded_user, config, wednesday):
        # Free: 2 a month, then 5 credits each.
        parent = create_task(funded_user, title='Clean', due_date=wednesday, rrule='FREQ=DAILY')
        create_task(funded_user, title='One', parent=parent)
        create_task(funded_user, title='Two', parent=parent)
        account = get_account(funded_user)
        assert month_usage(account, config)[0] == 3
        before = balance(funded_user)

        complete_task(parent)

        account = TodoAccount.objects.get(user=funded_user)
        assert account.month_task_count == 3 + 3  # the next occurrence and its two subtasks
        assert before - balance(funded_user) == 15

    def test_completing_always_succeeds_and_the_unpaid_occurrence_blocks_creation(
        self, user, config, wednesday, django_capture_on_commit_callbacks
    ):
        add_credits(user, 5, description='Top-up', source='test')
        first = create_task(user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')  # free
        complete_task(first)  # the 2nd: free
        second = occurrences(user)[1]
        complete_task(second)  # the 3rd costs 5: affordable
        third = occurrences(user)[2]
        assert third.unpaid_at is None and balance(user) == 0

        complete_task(third)  # the 4th costs 5 and the balance is 0

        fourth = occurrences(user)[3]
        assert fourth.unpaid_at is not None and fourth.charged_credits == 5
        assert TodoAccount.objects.get(user=user).creation_blocked_at is not None
        with pytest.raises(ValidationError, match='Top up'):
            create_task(user, title='Something else')

        with django_capture_on_commit_callbacks(execute=True):
            add_credits(user, 10, description='Top-up', source='test')

        fourth.refresh_from_db()
        assert fourth.unpaid_at is None
        assert TodoAccount.objects.get(user=user).creation_blocked_at is None
        assert create_task(user, title='Something else').number

    def test_subtasks_of_an_unpaid_occurrence_are_unpaid_too(self, user, config, wednesday):
        parent = create_task(user, title='Clean', due_date=wednesday, rrule='FREQ=DAILY')
        create_task(user, title='Sub', parent=parent)  # 2 free tasks used
        complete_task(parent)

        new_parent = occurrences(user)[1]
        assert new_parent.unpaid_at is not None
        assert new_parent.subtasks.get().unpaid_at is not None

    def test_a_refusal_creating_the_next_one_does_not_fail_the_completion(
        self, funded_user, config, wednesday
    ):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        config.max_open_tasks_per_user = 0
        config.save()

        complete_task(task)

        assert Task.objects.get(pk=task.pk).completed_at is not None
        assert len(occurrences(funded_user)) == 1
        assert Recurrence.objects.get().occurrences_created == 1  # nothing advanced


class TestAnOccurrenceSpawnsAtMostOnce:
    @pytest.mark.parametrize('mode', [RecurrenceMode.FIXED, RecurrenceMode.FROM_COMPLETION])
    def test_complete_uncomplete_complete_creates_and_charges_nothing_more(
        self, funded_user, wednesday, mode
    ):
        task = create_task(
            funded_user,
            title='Daily',
            due_date=wednesday,
            rrule='FREQ=DAILY',
            recurrence_mode=mode,
        )
        complete_task(task)
        spent = balance(funded_user)
        used = month_usage(get_account(funded_user), TodoSettings.load())
        state = Recurrence.objects.get()
        anchor = state.anchor_date

        uncomplete_task(task)
        complete_task(task)
        uncomplete_task(task)
        complete_task(task)

        assert len(occurrences(funded_user)) == 2
        state.refresh_from_db()
        assert (state.anchor_date, state.occurrences_created) == (anchor, 2)
        assert balance(funded_user) == spent
        assert month_usage(get_account(funded_user), TodoSettings.load()) == used
        assert Task.objects.get(pk=task.pk).completed_at is not None

    def test_the_new_occurrence_remembers_the_anchor_it_was_scheduled_for(
        self, funded_user, wednesday
    ):
        first = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        assert first.recurrence_anchor == wednesday

        complete_task(first)

        second = occurrences(funded_user)[1]
        assert second.recurrence_anchor == second.due_date == date(2026, 10, 29)

    def test_completing_an_older_open_occurrence_creates_nothing(self, funded_user, wednesday):
        first = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(first)
        second = occurrences(funded_user)[1]
        uncomplete_task(first)  # two open occurrences, the first one older
        complete_task(second)
        assert len(occurrences(funded_user)) == 3

        complete_task(first)

        assert len(occurrences(funded_user)) == 3
        assert Recurrence.objects.get().occurrences_created == 3

    def test_a_rule_change_makes_the_edited_task_the_one_that_spawns(self, funded_user, wednesday):
        first = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(first)
        second = occurrences(funded_user)[1]
        uncomplete_task(first)

        update_task(first, changes={'rrule': 'FREQ=WEEKLY'})
        complete_task(second)  # an older anchor now: nothing

        assert len(occurrences(funded_user)) == 2
        assert Task.objects.get(pk=first.pk).recurrence_anchor == wednesday
        complete_task(first)
        assert occurrences(funded_user)[2].due_date == date(2026, 11, 4)


class TestFailedNextOccurrence:
    def notices(self, user):
        return list(Notification.objects.filter(user=user, source='todo'))

    def test_a_refused_occurrence_tells_the_user_and_leaves_the_repeat_retryable(
        self, funded_user, config, wednesday
    ):
        task = create_task(funded_user, title='Pay “rent”', due_date=wednesday, rrule='FREQ=DAILY')
        config.max_open_tasks_per_user = 0
        config.save()

        complete_task(task)

        state = Recurrence.objects.get()
        assert (state.anchor_date, state.occurrences_created, state.ended_at) == (
            wednesday,
            1,
            None,
        )
        (notice,) = self.notices(funded_user)
        assert notice.title == 'Couldn\u2019t create the next \u201cPay \u201crent\u201d\u201d'
        assert 'open-task limit' in notice.body
        assert 'uncomplete and complete' in notice.body and 'manually' in notice.body
        assert notice.url == reverse('todo:task', args=[task.number])
        assert notice.key == failure_key(state)

        config.max_open_tasks_per_user = 100
        config.save()
        uncomplete_task(task)
        complete_task(task)  # there is room now

        assert len(occurrences(funded_user)) == 2
        assert self.notices(funded_user) == []  # the stale notice is cleared

    def test_the_subtasks_are_left_out_when_they_do_not_fit(self, funded_user, config, wednesday):
        parent = create_task(funded_user, title='Clean', due_date=wednesday, rrule='FREQ=DAILY')
        create_task(funded_user, title='Sub', parent=parent)
        config.max_open_tasks_per_user = 2
        config.save()

        complete_task(parent)

        _, copy = occurrences(funded_user)
        assert copy.due_date == date(2026, 10, 29)
        assert not copy.subtasks.exists()
        assert Task.objects.filter(title='Sub').count() == 1  # the partial copy was undone
        state = Recurrence.objects.get()
        assert (state.anchor_date, state.occurrences_created) == (date(2026, 10, 29), 2)
        (notice,) = self.notices(funded_user)
        assert 'no subtasks' in notice.title and 'Clean' in notice.title
        assert 'subtasks were not copied' in notice.body
        assert notice.key == failure_key(state)

    def test_a_task_without_subtasks_is_not_retried_and_a_clean_one_creates_no_notice(
        self, funded_user, wednesday
    ):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')

        complete_task(task)

        assert self.notices(funded_user) == []
        assert len(occurrences(funded_user)) == 2

    def test_a_failing_notification_does_not_fail_the_completion(
        self, funded_user, config, wednesday, monkeypatch
    ):
        from apps.core.services import notifications

        def boom(*args, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr(notifications, 'notify', boom)
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        config.max_open_tasks_per_user = 0
        config.save()

        complete_task(task)

        assert Task.objects.get(pk=task.pk).completed_at is not None


class TestRaceAndUncomplete:
    def test_two_completions_with_the_same_stale_task_create_one_occurrence(
        self, funded_user, wednesday
    ):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        stale = Task.objects.select_related('recurrence', 'user').get(pk=task.pk)

        first = on_completed(stale)
        second = on_completed(stale)

        assert first is not None and second is None
        assert len(occurrences(funded_user)) == 2
        assert Recurrence.objects.get().occurrences_created == 2

    def test_uncompleting_leaves_the_next_occurrence_in_place(self, funded_user, wednesday):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(task)
        balance_after = balance(funded_user)

        uncomplete_task(task)

        assert len(occurrences(funded_user)) == 2
        assert Task.objects.get(pk=task.pk).completed_at is None
        assert balance(funded_user) == balance_after  # nothing refunded

    def test_trashing_an_occurrence_does_not_end_the_repeat(self, funded_user, wednesday):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')

        trash_task(task)

        assert Recurrence.objects.get().ended_at is None

    def test_copy_extras_is_called_with_both_occurrences(self, funded_user, wednesday, monkeypatch):
        calls = []
        monkeypatch.setattr(recurrence, 'copy_extras', lambda old, new: calls.append((old, new)))
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')

        complete_task(task)

        assert [(old.pk, new.number) for old, new in calls] == [(task.pk, task.number + 1)]


class TestSettingTheRule:
    def test_a_rule_on_a_task_without_a_date_sets_the_first_date_on_or_after_today(
        self, funded_user, wednesday
    ):
        task = create_task(funded_user, title='Monday thing', rrule='FREQ=WEEKLY;BYDAY=MO')

        assert task.due_date == date(2026, 11, 2)
        assert Recurrence.objects.get().anchor_date == date(2026, 11, 2)

    def test_today_counts_when_the_rule_hits_it(self, funded_user, wednesday):
        task = create_task(funded_user, title='Wed', rrule='FREQ=WEEKLY;BYDAY=WE')

        assert task.due_date == wednesday

    def test_a_rule_on_a_task_with_a_date_anchors_at_that_date(self, funded_user, wednesday):
        task = create_task(
            funded_user, title='Rent', due_date=date(2026, 11, 5), rrule=MONTHLY_FIRST
        )

        assert task.due_date == date(2026, 11, 5)
        assert Recurrence.objects.get().anchor_date == date(2026, 11, 5)

    def test_editing_the_rule_sets_the_anchor_to_the_tasks_date(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule='FREQ=DAILY')
        update_task(task, changes={'due_date': date(2026, 11, 20)})
        assert Recurrence.objects.get().anchor_date == wednesday

        update_task(task, changes={'rrule': MONTHLY_FIRST, 'recurrence_mode': 'fixed'})

        state = Recurrence.objects.get()
        assert (state.rrule, state.anchor_date) == (MONTHLY_FIRST, date(2026, 11, 20))
        assert Recurrence.objects.count() == 1  # edited in place

    def test_changing_the_rule_restarts_the_count(self, funded_user, wednesday):
        first = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(first)
        second = occurrences(funded_user)[1]
        assert Recurrence.objects.get().occurrences_created == 2

        update_task(second, changes={'rrule': 'FREQ=DAILY;COUNT=2'})

        assert Recurrence.objects.get().occurrences_created == 0
        for _ in range(3):  # a new COUNT of 2 counts the occurrences made after the change
            complete_task(occurrences(funded_user)[-1])
        assert len(occurrences(funded_user)) == 4  # 2 before the change + 2 after it
        assert Recurrence.objects.get().ended_at is not None

    def test_the_same_rule_and_mode_do_not_restart_the_count(self, funded_user, wednesday):
        first = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(first)
        second = occurrences(funded_user)[1]

        update_task(second, changes={'rrule': 'FREQ=DAILY', 'title': 'Daily!'})

        assert Recurrence.objects.get().occurrences_created == 2

    def test_a_day_less_rule_keeps_the_intended_day_across_short_months(
        self, funded_user, freeze_now
    ):
        freeze_now(2027, 1, 20)
        task = create_task(
            funded_user, title='Invoice', due_date=date(2027, 1, 31), rrule='FREQ=MONTHLY'
        )
        dates = [task.due_date]

        for _ in range(4):
            complete_task(occurrences(funded_user)[-1])
            dates.append(occurrences(funded_user)[-1].due_date)

        assert dates == [
            date(2027, 1, 31),
            date(2027, 2, 28),
            date(2027, 3, 31),
            date(2027, 4, 30),
            date(2027, 5, 31),
        ]
        assert Recurrence.objects.get().anchor_day == 31

    def test_the_same_rule_and_mode_change_nothing(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule=MONTHLY_FIRST)
        update_task(task, changes={'due_date': date(2026, 11, 20)})

        update_task(
            task,
            changes={
                'rrule': 'freq=monthly;bymonthday=1',
                'recurrence_mode': 'fixed',
                'title': 'Rent!',
            },
        )

        assert Recurrence.objects.get().anchor_date == wednesday

    def test_changing_only_the_mode_edits_the_running_rule(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule=MONTHLY_FIRST)

        update_task(task, changes={'recurrence_mode': 'from_completion'})

        assert Recurrence.objects.get().mode == 'from_completion'

    def test_an_empty_rule_stops_repeating_and_keeps_the_task(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule=MONTHLY_FIRST)

        update_task(task, changes={'rrule': '', 'recurrence_mode': 'fixed'})

        assert Recurrence.objects.get().ended_at is not None
        assert task.active_recurrence is None
        assert Task.objects.filter(pk=task.pk).exists()

    def test_a_new_rule_after_stopping_makes_a_new_recurrence(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule=MONTHLY_FIRST)
        recurrence.stop(task)

        update_task(task, changes={'rrule': 'FREQ=DAILY'})

        assert Recurrence.objects.count() == 2
        assert task.active_recurrence is not None
        assert Recurrence.objects.filter(rrule='FREQ=DAILY', ended_at=None).count() == 1

    def test_a_bad_rule_or_mode_changes_nothing(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday)

        with pytest.raises(ValidationError) as bad_rule:
            update_task(task, changes={'title': 'New', 'rrule': 'FREQ=HOURLY'})
        with pytest.raises(ValidationError) as bad_mode:
            update_task(task, changes={'rrule': 'FREQ=DAILY', 'recurrence_mode': 'weekly'})

        assert (
            'rrule' in bad_rule.value.message_dict
            and 'recurrence_mode' in bad_mode.value.message_dict
        )
        task.refresh_from_db()
        assert task.title == 'Rent' and Recurrence.objects.count() == 0

    def test_a_subtask_cannot_repeat(self, funded_user, wednesday):
        parent = create_task(funded_user, title='Parent')

        with pytest.raises(ValidationError, match='cannot repeat'):
            create_task(funded_user, title='Sub', parent=parent, rrule='FREQ=DAILY')
        sub = create_task(funded_user, title='Sub', parent=parent)
        with pytest.raises(ValidationError, match='cannot repeat'):
            update_task(sub, changes={'rrule': 'FREQ=DAILY'})
        with pytest.raises(ValidationError, match='cannot repeat'):
            sub.recurrence = Recurrence.objects.create(
                user=funded_user, rrule='FREQ=DAILY', anchor_date=wednesday
            )
            sub.full_clean(exclude=['user', 'number'])

    def test_a_repeating_task_cannot_become_a_subtask(self, funded_user, wednesday):
        parent = create_task(funded_user, title='Parent')
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')

        with pytest.raises(ValidationError, match='cannot become a subtask'):
            update_task(task, changes={'parent': parent})
        with pytest.raises(ValidationError, match='cannot repeat'):
            update_task(task, changes={'parent': parent, 'rrule': 'FREQ=WEEKLY'})

    def test_only_an_open_task_can_be_given_a_rule(self, funded_user, wednesday):
        task = create_task(funded_user, title='Done')
        complete_task(task)

        with pytest.raises(ValidationError, match='open task'):
            update_task(task, changes={'rrule': 'FREQ=DAILY'})

    def test_the_recurrence_must_be_the_users_own(self, funded_user, other_user, wednesday):
        theirs = Recurrence.objects.create(
            user=other_user, rrule='FREQ=DAILY', anchor_date=wednesday
        )

        with pytest.raises(ValidationError):
            create_task(funded_user, title='Mine', recurrence=theirs)

    def test_deleting_the_recurrence_keeps_the_tasks(self, funded_user, wednesday):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')

        Recurrence.objects.all().delete()

        task.refresh_from_db()
        assert task.recurrence_id is None


class TestPages:
    def test_the_form_round_trips_a_rule_and_mode(self, web, funded_user, wednesday):
        response = web.post(
            reverse('todo:create'),
            {
                'title': 'Rotate tires',
                'view': 'all',
                'rrule': 'FREQ=MONTHLY;INTERVAL=3',
                'recurrence_mode': 'from_completion',
            },
            **HTMX,
        )

        assert response.status_code == 200
        task = Task.objects.get(user=funded_user, title='Rotate tires')
        assert task.recurrence.rrule == 'FREQ=MONTHLY;INTERVAL=3'
        assert task.recurrence.mode == 'from_completion'
        assert task.due_date == wednesday  # a rule with no date: the first date, today
        body = response.content.decode()
        assert 'Every 3 months' in body and 'from completion' in body

        page = web.get(reverse('todo:task', args=[task.number])).content.decode()
        assert 'Every 3 months' in page and 'From completion' in page
        assert 'name="rrule" value="FREQ=MONTHLY;INTERVAL=3"' in page
        assert 'name="recurrence_mode" value="from_completion"' in page
        assert 'Stop repeating' in page

    def test_an_unsupported_rule_is_a_422_that_creates_nothing(self, web, funded_user):
        response = web.post(
            reverse('todo:create'),
            {'title': 'Bad', 'view': 'all', 'rrule': 'FREQ=HOURLY'},
            **HTMX,
        )

        assert response.status_code == 422
        assert 'Repeat: Unsupported repeat rule.' in response.content.decode()
        assert not Task.objects.filter(user=funded_user).exists()

    def test_the_details_form_edits_the_rule(self, web, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule='FREQ=DAILY')

        response = web.post(
            reverse('todo:edit', args=[task.number]),
            {
                'part': 'details',
                'ctx': 'page',
                'due_date': '2026-10-28',
                'priority': '0',
                'rrule': MONTHLY_FIRST,
                'recurrence_mode': 'fixed',
            },
            **HTMX,
        )

        assert response.status_code == 200
        assert 'Every month on the 1st' in response.content.decode()
        assert Recurrence.objects.get().rrule == MONTHLY_FIRST

    def test_an_edit_without_the_repeat_fields_leaves_the_rule_alone(
        self, web, funded_user, wednesday
    ):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule='FREQ=DAILY')

        web.post(
            reverse('todo:edit', args=[task.number]), {'part': 'details', 'priority': '1'}, **HTMX
        )
        web.post(
            reverse('todo:edit', args=[task.number]), {'part': 'title', 'title': 'Rent 2'}, **HTMX
        )

        assert Recurrence.objects.get().ended_at is None

    def test_an_empty_rule_in_the_details_form_stops_repeating(self, web, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule='FREQ=DAILY')

        web.post(
            reverse('todo:edit', args=[task.number]),
            {'part': 'details', 'ctx': 'page', 'rrule': '', 'recurrence_mode': 'fixed'},
            **HTMX,
        )

        assert Recurrence.objects.get().ended_at is not None

    def test_the_chip_shows_on_rows_and_the_mode_in_its_tooltip(self, web, funded_user, wednesday):
        create_task(funded_user, title='Rent', due_date=wednesday, rrule=MONTHLY_FIRST)

        page = web.get(reverse('todo:today')).content.decode()

        assert 'fa-repeat' in page and 'Every month on the 1st' in page
        assert 'title="Fixed schedule"' in page

    def test_stop_repeating(self, web, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule=MONTHLY_FIRST)

        response = web.post(
            reverse('todo:recurrence-stop', args=[task.number]), {'ctx': 'page'}, **HTMX
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert 'Stop repeating' not in body and 'no longer repeats' in body
        assert Recurrence.objects.get().ended_at is not None
        assert Task.objects.filter(pk=task.pk, deleted_at__isnull=True).exists()

    def test_stop_repeating_is_the_owners_and_post_only(self, web, other_user, wednesday):
        theirs = create_task(other_user, title='Theirs', due_date=wednesday, rrule='FREQ=DAILY')

        assert web.post(reverse('todo:recurrence-stop', args=[theirs.number])).status_code == 404
        assert web.get(reverse('todo:recurrence-stop', args=[theirs.number])).status_code == 405
        assert Recurrence.objects.get().ended_at is None

    def test_trashing_with_also_stop_repeating(self, web, funded_user, wednesday):
        keep = create_task(funded_user, title='Keep', due_date=wednesday, rrule='FREQ=DAILY')
        stop = create_task(funded_user, title='Stop', due_date=wednesday, rrule='FREQ=WEEKLY')

        web.post(reverse('todo:trash-task', args=[keep.number]))
        web.post(reverse('todo:trash-task', args=[stop.number]), {'stop_recurrence': '1'})

        assert Task.objects.get(pk=keep.pk).deleted_at is not None
        assert Task.objects.get(pk=keep.pk).recurrence.ended_at is None
        assert Task.objects.get(pk=stop.pk).recurrence.ended_at is not None

    def test_the_trash_button_of_a_repeating_task_offers_to_stop(self, web, funded_user, wednesday):
        create_task(funded_user, title='Rent', due_date=wednesday, rrule=MONTHLY_FIRST)
        create_task(funded_user, title='Plain', due_date=wednesday)

        page = web.get(reverse('todo:today')).content.decode()

        assert page.count('data-checkbox-name="stop_recurrence"') == 1

    def test_the_trash_button_offers_to_stop_only_for_the_only_open_occurrence(
        self, web, funded_user, wednesday
    ):
        first = create_task(funded_user, title='Rent', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(first)
        uncomplete_task(first)  # two open occurrences now
        assert Task.objects.filter(user=funded_user, completed_at__isnull=True).count() == 2

        rows = web.get(reverse('todo:all')).content.decode()
        page = web.get(reverse('todo:task', args=[first.number])).content.decode()
        assert 'data-checkbox-name="stop_recurrence"' not in rows
        assert 'data-checkbox-name="stop_recurrence"' not in page

        trash_task(occurrences(funded_user)[1])  # leaves the first as the only one
        rows = web.get(reverse('todo:all')).content.decode()
        page = web.get(reverse('todo:task', args=[first.number])).content.decode()
        assert rows.count('data-checkbox-name="stop_recurrence"') == 1
        assert page.count('data-checkbox-name="stop_recurrence"') == 1

    def test_a_stopped_repeat_does_not_offer_to_stop(self, web, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=wednesday, rrule='FREQ=DAILY')
        recurrence.stop(task)

        page = web.get(reverse('todo:all')).content.decode()

        assert 'data-checkbox-name="stop_recurrence"' not in page

    def test_completing_from_the_page_creates_the_next_occurrence(
        self, web, funded_user, wednesday
    ):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')

        response = web.post(reverse('todo:complete', args=[task.number]), {'view': 'today'}, **HTMX)

        assert response.status_code == 200
        assert len(occurrences(funded_user)) == 2

    def test_the_uncomplete_note_and_the_completed_view(self, web, funded_user, wednesday):
        task = create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')
        complete_task(task)

        page = web.get(reverse('todo:task', args=[task.number])).content.decode()
        completed = web.get(reverse('todo:completed')).content.decode()

        assert 'keeps the next occurrence' in page
        assert f'id="task-{task.number}"' in completed  # the completed one is its own row
        assert f'id="task-{task.number + 1}"' not in completed  # the open next one is not

    def test_a_subtask_form_has_no_repeat_but_the_page_still_renders(self, web, funded_user):
        parent = create_task(funded_user, title='Parent')
        sub = create_task(funded_user, title='Sub', parent=parent)

        assert web.get(reverse('todo:task', args=[sub.number])).status_code == 200

    def test_admin_lists_recurrences(self, admin_client, funded_user, wednesday):
        create_task(funded_user, title='Daily', due_date=wednesday, rrule='FREQ=DAILY')

        recurrences = admin_client.get('/admin/todo/recurrence/')
        tasks = admin_client.get('/admin/todo/task/')

        assert recurrences.status_code == 200 and 'FREQ=DAILY' in recurrences.content.decode()
        assert tasks.status_code == 200
        assert admin_client.get('/admin/todo/recurrence/add/').status_code == 403


def test_make_task_helper_still_builds_plain_tasks(user):
    assert make_task(user).recurrence_id is None
    assert make_user().pk
