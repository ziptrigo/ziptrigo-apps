"""Reminders (issue #182): the fire time, the defaults, adding/editing/removing, rescheduling,
carrying over to the next occurrence, delivering in-app and by email, the stale window, the job, the
admin and the pages."""

from datetime import date, time, timedelta
from html import unescape
from uuid import uuid4

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.services.timezones import set_timezone
from apps.core.models import Notification
from apps.core.scheduler import get_jobs

from .. import jobs
from ..limits import MAX_REMINDER_MINUTES, MAX_REMINDERS_PER_TASK
from ..models import (
    Reminder,
    ReminderDelivery,
    Task,
    TaskSource,
)
from ..services import (
    complete_task,
    create_task,
    get_account,
    postpone_task,
    reminders,
    reschedule_overdue,
    trash_task,
    uncomplete_task,
    update_default_due_time,
    update_task,
)
from .factories import make_task, make_user, utc

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
TODAY = date(2026, 10, 28)  # a Wednesday; `wednesday` freezes now at 12:00 UTC


def reminders_of(task: Task) -> list[Reminder]:
    return list(Reminder.objects.filter(task=task).order_by('minutes_before'))


def timed_task(user, *, hour=14, minute=30, **fields) -> Task:
    """A task due today at hour:minute (the user's wall clock) with the default 5 minute reminder."""
    return create_task(
        user, title='Pay rent', due_date=TODAY, due_time=time(hour, minute), **fields
    )


class TestFireAt:
    def test_a_timed_task_fires_before_its_time(self, funded_user, wednesday):
        funded_user.timezone = 'America/Chicago'  # CDT, UTC-5, until 1 November
        task = make_task(funded_user, due_date=TODAY, due_time=time(14, 30))

        fire = reminders.fire_at_for(task, 5, get_account(funded_user))

        assert fire == utc(2026, 10, 28, 19, 25)

    def test_a_date_only_task_uses_the_default_due_time(self, funded_user, wednesday):
        account = get_account(funded_user)
        update_default_due_time(account, time(8, 15))
        task = make_task(funded_user, due_date=TODAY)

        assert reminders.fire_at_for(task, 15, account) == utc(2026, 10, 28, 8, 0)

    def test_zero_minutes_is_the_due_time(self, funded_user):
        task = make_task(funded_user, due_date=TODAY, due_time=time(9, 0))

        assert reminders.fire_at_for(task, 0, get_account(funded_user)) == utc(2026, 10, 28, 9, 0)

    def test_a_task_without_a_date_has_none(self, funded_user):
        assert reminders.fire_at_for(make_task(funded_user), 5, get_account(funded_user)) is None

    # The clocks go back in Chicago on 1 November 2026, at 02:00 CDT (07:00 UTC) -> 01:00 CST.
    @pytest.mark.parametrize(
        ('due_date', 'minutes', 'expected_utc'),
        [
            # Whole days are calendar days on the wall clock: 09:00 on the 1st (CST, UTC-6) a day
            # before is 09:00 on 31 October (CDT, UTC-5), 25 elapsed hours earlier.
            (date(2026, 11, 1), 1440, utc(2026, 10, 31, 14, 0)),
            # A week before 3 November 09:00 CST is 27 October 09:00 CDT (169 elapsed hours).
            (date(2026, 11, 3), 7 * 1440, utc(2026, 10, 27, 14, 0)),
            (date(2026, 11, 3), 2 * 1440, utc(2026, 11, 1, 15, 0)),  # no change in between: 48 h
        ],
    )
    def test_whole_days_are_calendar_days_across_a_clock_change(
        self, funded_user, due_date, minutes, expected_utc
    ):
        funded_user.timezone = 'America/Chicago'
        task = make_task(funded_user, due_date=due_date, due_time=time(9, 0))

        fire = reminders.fire_at_for(task, minutes, get_account(funded_user))

        assert fire == expected_utc
        assert fire is not None
        assert fire.astimezone(reminders.user_tz(funded_user)).hour == 9  # the same wall clock

    def test_a_date_only_task_keeps_its_default_hour_across_a_clock_change(self, funded_user):
        funded_user.timezone = 'America/Chicago'
        task = make_task(funded_user, due_date=date(2026, 11, 1))  # default due time 09:00

        fire = reminders.fire_at_for(task, 1440, get_account(funded_user))

        assert fire == utc(2026, 10, 31, 14, 0)  # 09:00 CDT

    @pytest.mark.parametrize('minutes', [180, 90])
    def test_minutes_and_hours_are_real_elapsed_time_across_a_clock_change(
        self, funded_user, minutes
    ):
        # 03:00 CST on 1 November is 09:00 UTC. Three hours earlier is 06:00 UTC, which is 01:00
        # CDT (the hour before the change): only 2 hours on the wall clock, 3 real ones.
        funded_user.timezone = 'America/Chicago'
        task = make_task(funded_user, due_date=date(2026, 11, 1), due_time=time(3, 0))

        fire = reminders.fire_at_for(task, minutes, get_account(funded_user))

        assert fire == utc(2026, 11, 1, 9, 0) - timedelta(minutes=minutes)
        assert fire is not None
        if minutes == 180:
            assert fire == utc(2026, 11, 1, 6, 0)
            assert fire.astimezone(reminders.user_tz(funded_user)).hour == 1

    def test_the_wall_clock_follows_the_users_zone(self, funded_user):
        task = make_task(funded_user, due_date=TODAY, due_time=time(9, 0))
        account = get_account(funded_user)
        in_utc = reminders.fire_at_for(task, 0, account)
        assert in_utc is not None
        funded_user.timezone = 'Asia/Tokyo'

        assert reminders.fire_at_for(task, 0, account) == in_utc - timedelta(hours=9)

    @pytest.mark.parametrize(
        ('minutes', 'text'),
        [
            (0, 'At time of task'),
            (1, '1 minute before'),
            (5, '5 minutes before'),
            (60, '1 hour before'),
            (90, '90 minutes before'),
            (120, '2 hours before'),
            (1440, '1 day before'),
            (10080, '7 days before'),
        ],
    )
    def test_describe_offset(self, minutes, text):
        assert reminders.describe_offset(minutes) == text


class TestDefaults:
    @pytest.mark.parametrize('source', [s.value for s in TaskSource])
    def test_a_new_task_with_a_date_gets_the_default_reminder_from_any_source(
        self, funded_user, wednesday, source
    ):
        task = create_task(funded_user, title='x', due_date=TODAY, source=source)

        [reminder] = reminders_of(task)
        assert (reminder.minutes_before, reminder.channel) == (5, 'in_app')
        assert reminder.sent_at is None

    def test_a_task_without_a_date_gets_none(self, funded_user):
        assert reminders_of(create_task(funded_user, title='x')) == []

    def test_a_subtask_without_a_date_gets_none(self, funded_user):
        parent = create_task(funded_user, title='p', due_date=TODAY)
        child = create_task(funded_user, title='c', parent=parent)

        assert reminders_of(child) == []

    def test_the_accounts_default_is_used(self, funded_user, wednesday):
        reminders.set_defaults(get_account(funded_user), minutes=1440, channel='both')

        [reminder] = reminders_of(create_task(funded_user, title='x', due_date=TODAY))

        assert (reminder.minutes_before, reminder.channel) == (1440, 'both')

    def test_no_default_means_no_reminder(self, funded_user):
        reminders.set_defaults(get_account(funded_user), minutes=None, channel='in_app')

        assert reminders_of(create_task(funded_user, title='x', due_date=TODAY)) == []

    def test_the_default_can_be_zero_minutes(self, funded_user):
        reminders.set_defaults(get_account(funded_user), minutes=0, channel='in_app')

        [reminder] = reminders_of(create_task(funded_user, title='x', due_date=TODAY))

        assert reminder.minutes_before == 0

    def test_a_task_that_first_gains_a_date_gets_the_default(self, funded_user, wednesday):
        task = create_task(funded_user, title='x')

        update_task(task, changes={'due_date': TODAY})

        assert [r.minutes_before for r in reminders_of(task)] == [5]

    def test_a_repeating_task_without_a_date_gets_one_for_its_first_date(
        self, funded_user, wednesday
    ):
        task = create_task(funded_user, title='x', rrule='FREQ=DAILY')

        assert [r.minutes_before for r in reminders_of(task)] == [5]

    def test_a_task_that_already_had_a_date_does_not_get_it_back(self, funded_user, wednesday):
        task = create_task(funded_user, title='x', due_date=TODAY)
        reminders.delete_reminder(reminders_of(task)[0])

        update_task(task, changes={'due_date': TODAY + timedelta(days=1)})

        assert reminders_of(task) == []

    def test_postponing_a_task_without_a_date_gives_it_the_default(self, funded_user, wednesday):
        task = create_task(funded_user, title='x')

        postpone_task(task, preset='tomorrow')

        [reminder] = reminders_of(task)
        assert reminder.fire_at == utc(2026, 10, 29, 8, 55)

    def test_a_completed_task_is_not_given_one(self, funded_user, wednesday):
        task = create_task(funded_user, title='x')
        complete_task(task)

        update_task(task, changes={'due_date': TODAY})

        assert reminders_of(task) == []

    def test_set_defaults_validates(self, funded_user):
        account = get_account(funded_user)
        with pytest.raises(ValidationError):
            reminders.set_defaults(account, minutes=MAX_REMINDER_MINUTES + 1, channel='in_app')
        with pytest.raises(ValidationError):
            reminders.set_defaults(account, minutes=5, channel='sms')

    def test_a_new_account_defaults_to_five_minutes_in_app(self, funded_user):
        account = get_account(funded_user)

        assert (account.default_reminder_minutes, account.default_reminder_channel) == (5, 'in_app')


class TestAddUpdateDelete:
    def test_add_computes_the_fire_time(self, funded_user, wednesday):
        task = timed_task(funded_user)

        reminder = reminders.add_reminder(task, minutes_before=60, channel='email')

        assert reminder.fire_at == utc(2026, 10, 28, 13, 30)
        assert (reminder.channel, reminder.sent_at) == ('email', None)

    def test_a_task_needs_a_due_date(self, funded_user):
        task = create_task(funded_user, title='x')

        with pytest.raises(ValidationError, match='Set a due date first.'):
            reminders.add_reminder(task, minutes_before=5, channel='in_app')

    def test_one_reminder_per_time(self, funded_user, wednesday):
        task = timed_task(funded_user)

        with pytest.raises(ValidationError, match='There is already a reminder at that time.'):
            reminders.add_reminder(task, minutes_before=5, channel='email')

    def test_at_most_ten_reminders(self, funded_user, wednesday):
        task = timed_task(funded_user)
        for minutes in range(10, 10 * MAX_REMINDERS_PER_TASK, 10):
            reminders.add_reminder(task, minutes_before=minutes, channel='in_app')
        assert Reminder.objects.filter(task=task).count() == MAX_REMINDERS_PER_TASK

        with pytest.raises(ValidationError, match='at most 10'):
            reminders.add_reminder(task, minutes_before=999, channel='in_app')

    @pytest.mark.parametrize('minutes', [-1, MAX_REMINDER_MINUTES + 1, 'soon', None])
    def test_the_time_is_bounded(self, funded_user, wednesday, minutes):
        task = timed_task(funded_user)

        with pytest.raises(ValidationError):
            reminders.add_reminder(task, minutes_before=minutes, channel='in_app')

    def test_thirty_days_is_allowed(self, funded_user, wednesday):
        task = timed_task(funded_user)

        reminder = reminders.add_reminder(
            task, minutes_before=MAX_REMINDER_MINUTES, channel='in_app'
        )

        assert reminder.fire_at == utc(2026, 9, 28, 14, 30)

    def test_the_channel_must_be_known(self, funded_user, wednesday):
        task = timed_task(funded_user)

        with pytest.raises(ValidationError):
            reminders.add_reminder(task, minutes_before=10, channel='sms')

    @pytest.mark.parametrize('how', ['completed', 'trashed'])
    def test_only_an_open_task_has_reminders(self, funded_user, wednesday, how):
        task = timed_task(funded_user)
        (complete_task if how == 'completed' else trash_task)(task)

        with pytest.raises(ValidationError, match='open task'):
            reminders.add_reminder(task, minutes_before=10, channel='in_app')

    def test_a_late_reminder_is_still_unsent_so_it_fires_at_the_next_run(
        self, funded_user, wednesday
    ):
        task = timed_task(funded_user, hour=12, minute=2)

        reminder = reminders.add_reminder(task, minutes_before=10, channel='in_app')  # 11:52

        assert reminder.fire_at < wednesday_noon() and reminder.sent_at is None

    def test_a_reminder_more_than_a_day_overdue_is_created_sent(self, funded_user, wednesday):
        task = timed_task(funded_user)

        reminder = reminders.add_reminder(task, minutes_before=2 * 1440, channel='in_app')

        assert reminder.sent_at == wednesday_noon() and reminder.skipped
        assert reminders.deliver_due(wednesday_noon()) == 0
        assert not Notification.objects.exists()
        delivery = ReminderDelivery.objects.get(reminder=reminder)  # why it never fired
        assert (delivery.note, delivery.fire_token) == ('skipped', None)

    def test_update_changes_the_time_and_arms_it_again(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]
        Reminder.objects.filter(pk=reminder.pk).update(sent_at=wednesday_noon())
        reminder.refresh_from_db()

        reminders.update_reminder(reminder, minutes_before=30)

        reminder.refresh_from_db()
        assert reminder.minutes_before == 30
        assert reminder.fire_at == utc(2026, 10, 28, 14, 0)
        assert reminder.sent_at is None

    def test_update_to_a_taken_time_is_refused(self, funded_user, wednesday):
        task = timed_task(funded_user)
        other = reminders.add_reminder(task, minutes_before=15, channel='in_app')

        with pytest.raises(ValidationError, match='already a reminder'):
            reminders.update_reminder(other, minutes_before=5)

    def test_update_keeps_its_own_time(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]

        reminders.update_reminder(reminder, minutes_before=5, channel='both')

        reminder.refresh_from_db()
        assert (reminder.minutes_before, reminder.channel) == (5, 'both')

    def test_a_channel_change_leaves_a_sent_reminder_sent(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]
        Reminder.objects.filter(pk=reminder.pk).update(sent_at=wednesday_noon())
        reminder.refresh_from_db()

        reminders.update_reminder(reminder, channel='email')

        reminder.refresh_from_db()
        assert reminder.channel == 'email' and reminder.sent_at is not None

    def test_delete_keeps_the_history(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        delivery = ReminderDelivery.objects.get()

        reminders.delete_reminder(reminder)

        delivery.refresh_from_db()
        assert delivery.reminder_id is None and delivery.task_number == task.number
        assert not Reminder.objects.filter(task=task).exists()

    def test_a_completed_task_cannot_be_edited(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]
        complete_task(task)

        with pytest.raises(ValidationError):
            reminders.update_reminder(reminder, channel='email')
        with pytest.raises(ValidationError):
            reminders.delete_reminder(reminder)


def wednesday_noon():
    return utc(2026, 10, 28, 12, 0)


class TestReschedule:
    def test_editing_the_date_moves_the_reminders(self, funded_user, wednesday):
        task = timed_task(funded_user)

        update_task(task, changes={'due_date': date(2026, 10, 30)})

        assert reminders_of(task)[0].fire_at == utc(2026, 10, 30, 14, 25)

    def test_editing_the_time_moves_the_reminders(self, funded_user, wednesday):
        task = timed_task(funded_user)

        update_task(task, changes={'due_time': time(18, 0)})

        assert reminders_of(task)[0].fire_at == utc(2026, 10, 28, 17, 55)

    def test_a_change_that_leaves_the_time_alone_changes_nothing(self, funded_user, wednesday):
        task = timed_task(funded_user)
        before = reminders_of(task)[0].fire_at

        update_task(task, changes={'title': 'Pay rent now', 'due_date': TODAY})

        assert reminders_of(task)[0].fire_at == before

    def test_a_sent_reminder_is_left_alone_when_the_new_time_is_not_ahead(
        self, funded_user, wednesday
    ):
        task = timed_task(funded_user)  # due 14:30, reminder 14:25
        Reminder.objects.filter(task=task).update(sent_at=wednesday_noon())

        update_task(task, changes={'due_date': date(2026, 10, 27)})  # moved back a day

        [reminder] = reminders_of(task)
        assert reminder.sent_at == wednesday_noon()
        assert reminder.fire_at == utc(2026, 10, 27, 14, 25)

    def test_clearing_the_date_deletes_the_reminders(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.add_reminder(task, minutes_before=60, channel='email')
        assert len(reminders_of(task)) == 2

        update_task(task, changes={'due_date': None})

        assert reminders_of(task) == []

    def test_postponing_moves_them(self, funded_user, wednesday):
        task = timed_task(funded_user)

        postpone_task(task, preset='tomorrow')

        assert reminders_of(task)[0].fire_at == utc(2026, 10, 29, 14, 25)

    def test_rescheduling_all_overdue_moves_them(self, funded_user, wednesday):
        # Due yesterday evening: its reminder is 13 hours overdue, inside the day it is created unsent.
        task = create_task(
            funded_user, title='x', due_date=date(2026, 10, 27), due_time=time(23, 0)
        )
        assert reminders_of(task)[0].fire_at == utc(2026, 10, 27, 22, 55)

        assert reschedule_overdue(funded_user) == 1

        assert reminders_of(task)[0].fire_at == utc(2026, 10, 28, 22, 55)

    def test_changing_the_default_due_time_moves_date_only_tasks_only(
        self, funded_user, wednesday, django_capture_on_commit_callbacks
    ):
        date_only = create_task(funded_user, title='a', due_date=TODAY)
        timed = timed_task(funded_user)

        with django_capture_on_commit_callbacks(execute=True):
            update_default_due_time(get_account(funded_user), time(10, 0))

        assert reminders_of(date_only)[0].fire_at == utc(2026, 10, 28, 9, 55)
        assert reminders_of(timed)[0].fire_at == utc(2026, 10, 28, 14, 25)

    def test_saving_the_same_default_due_time_changes_nothing(
        self, funded_user, wednesday, monkeypatch, django_capture_on_commit_callbacks
    ):
        called = []
        monkeypatch.setattr(reminders, 'reschedule_user', lambda *a, **k: called.append(1))

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            update_default_due_time(get_account(funded_user), time(9, 0))

        assert called == [] and callbacks == []

    def test_a_new_time_zone_moves_every_reminder(
        self, funded_user, wednesday, django_capture_on_commit_callbacks
    ):
        task = timed_task(funded_user)
        sent = timed_task(funded_user, hour=16)
        Reminder.objects.filter(task=sent).update(sent_at=wednesday_noon())
        assert reminders_of(task)[0].fire_at == utc(2026, 10, 28, 14, 25)

        with django_capture_on_commit_callbacks(execute=True):
            assert set_timezone(funded_user, 'America/Chicago')

        assert reminders_of(task)[0].fire_at == utc(2026, 10, 28, 19, 25)
        assert reminders_of(sent)[0].fire_at == utc(2026, 10, 28, 16, 25)  # sent: untouched

    def test_the_zone_receiver_is_connected_once(self):
        from apps.accounts.signals import timezone_changed

        found = [r for r in timezone_changed.receivers if r[0][0] == 'todo.reschedule_reminders']

        assert len(found) == 1

    def test_the_task_ignores_an_unknown_user(self):
        reminders.reschedule_user_reminders.call(str(uuid4()))
        reminders.reschedule_user_reminders.call('not-a-uuid')

    def test_completed_tasks_are_recomputed_too(self, funded_user, wednesday):
        task = timed_task(funded_user)
        complete_task(task)
        funded_user.timezone = 'America/Chicago'

        reminders.reschedule_user(funded_user)

        assert reminders_of(task)[0].fire_at == utc(2026, 10, 28, 19, 25)


class TestCopyExtras:
    def test_the_next_occurrence_gets_the_same_reminders(self, funded_user, wednesday):
        task = create_task(
            funded_user,
            title='Rent',
            due_date=TODAY,
            due_time=time(9, 0),
            rrule='FREQ=WEEKLY',
        )
        Reminder.objects.filter(task=task).delete()
        reminders.add_reminder(task, minutes_before=15, channel='email')
        reminders.add_reminder(task, minutes_before=1440, channel='in_app')
        Reminder.objects.filter(task=task).update(sent_at=wednesday_noon())

        complete_task(task)

        nxt = Task.objects.get(user=funded_user, source='recurrence')
        copied = reminders_of(nxt)
        assert [(r.minutes_before, r.channel) for r in copied] == [
            (15, 'email'),
            (1440, 'in_app'),
        ]
        assert all(r.sent_at is None for r in copied)
        assert [r.fire_at for r in copied] == [
            utc(2026, 11, 4, 8, 45),
            utc(2026, 11, 3, 9, 0),
        ]

    def test_an_occurrence_has_no_reminder_when_its_predecessor_had_none(
        self, funded_user, wednesday
    ):
        task = create_task(funded_user, title='Rent', due_date=TODAY, rrule='FREQ=WEEKLY')
        Reminder.objects.filter(task=task).delete()

        complete_task(task)

        nxt = Task.objects.get(user=funded_user, source='recurrence')
        assert reminders_of(nxt) == []

    def test_the_default_is_not_added_twice(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=TODAY, rrule='FREQ=WEEKLY')

        complete_task(task)

        nxt = Task.objects.get(user=funded_user, source='recurrence')
        assert [r.minutes_before for r in reminders_of(nxt)] == [5]


class TestDeliverDue:
    def test_in_app_creates_a_notification_and_a_delivery(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]

        assert reminders.deliver_due(utc(2026, 10, 28, 14, 26)) == 1

        notification = Notification.objects.get(user=funded_user)
        assert notification.source == 'todo'
        assert notification.title == 'Pay rent'
        assert notification.body == 'Due 28 Oct 2026, 14:30'
        assert notification.url == reverse('todo:task', args=[task.number])
        reminder.refresh_from_db()
        assert reminder.sent_at == utc(2026, 10, 28, 14, 26)
        delivery = ReminderDelivery.objects.get()
        assert (delivery.channel, delivery.note, delivery.notification_id) == (
            'in_app',
            '',
            notification.pk,
        )
        assert (delivery.task_number, delivery.task_title) == (task.number, 'Pay rent')
        assert delivery.fire_at == reminder.fire_at and delivery.user == funded_user
        assert delivery.sent_at == utc(2026, 10, 28, 14, 26)

    def test_a_date_only_task_says_due_today(self, funded_user, wednesday):
        create_task(funded_user, title='Rent', due_date=TODAY)

        reminders.deliver_due(utc(2026, 10, 28, 9, 0))

        assert Notification.objects.get().body == 'Due today'

    def test_a_date_only_task_due_another_day_says_the_date(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=date(2026, 10, 30))
        reminders.add_reminder(task, minutes_before=1440, channel='in_app')

        reminders.deliver_due(utc(2026, 10, 29, 9, 0))

        assert Notification.objects.get().body == 'Due 30 Oct 2026'

    def test_a_long_title_is_cut_for_the_notification(self, funded_user, wednesday):
        create_task(funded_user, title='x' * 300, due_date=TODAY)

        reminders.deliver_due(utc(2026, 10, 28, 9, 0))

        assert len(Notification.objects.get().title) == 120

    def test_a_reminder_is_delivered_once(self, funded_user, wednesday):
        timed_task(funded_user)

        assert reminders.deliver_due(utc(2026, 10, 28, 14, 26)) == 1
        assert reminders.deliver_due(utc(2026, 10, 28, 14, 27)) == 0

        assert Notification.objects.count() == 1 and ReminderDelivery.objects.count() == 1

    def test_the_claim_loses_to_a_run_that_got_there_first(
        self, funded_user, wednesday, monkeypatch
    ):
        task = timed_task(funded_user)
        real = reminders._deliver
        calls = []
        monkeypatch.setattr(reminders, '_deliver', lambda *a: calls.append(a) or real(*a))
        original = Reminder.objects.filter

        # Another run claims the reminder between this run's read and its claim.
        def racing_filter(*args, **kwargs):
            if kwargs.get('sent_at__isnull') is True and 'pk' in kwargs:
                Reminder.objects.filter(pk=kwargs['pk']).update(sent_at=wednesday_noon())
            return original(*args, **kwargs)

        monkeypatch.setattr(Reminder.objects, 'filter', racing_filter)

        assert reminders.deliver_due(utc(2026, 10, 28, 14, 26)) == 0
        assert calls == [] and not Notification.objects.filter(user=task.user).exists()

    def test_nothing_is_delivered_before_its_time(self, funded_user, wednesday):
        timed_task(funded_user)

        assert reminders.deliver_due(utc(2026, 10, 28, 14, 24)) == 0

    @pytest.mark.parametrize('how', ['completed', 'trashed'])
    def test_a_task_that_is_not_open_is_skipped(self, funded_user, wednesday, how):
        task = timed_task(funded_user)
        (complete_task if how == 'completed' else trash_task)(task)

        assert reminders.deliver_due(utc(2026, 10, 28, 14, 26)) == 0

        assert not Notification.objects.exists()
        assert reminders_of(task)[0].sent_at is None  # left alone, not claimed

    def test_uncompleting_inside_the_window_makes_it_live_again(self, funded_user, wednesday):
        task = timed_task(funded_user)
        complete_task(task)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        uncomplete_task(task)

        assert reminders.deliver_due(utc(2026, 10, 28, 14, 40)) == 1

    @pytest.mark.parametrize('field', ['is_active', 'status'])
    def test_an_inactive_user_is_skipped(self, funded_user, wednesday, field):
        timed_task(funded_user)
        if field == 'is_active':
            funded_user.is_active = False
        else:
            funded_user.status = funded_user.STATUS_INACTIVE
        funded_user.save()

        assert reminders.deliver_due(utc(2026, 10, 28, 14, 26)) == 0

        assert not Notification.objects.exists()

    def test_a_failing_reminder_does_not_stop_the_others(self, funded_user, wednesday, monkeypatch):
        bad = timed_task(funded_user)
        good = create_task(funded_user, title='Other', due_date=TODAY, due_time=time(14, 30))
        real = reminders.notifications.notify

        def notify(user, **kwargs):
            if kwargs['title'] == bad.title:
                raise RuntimeError('boom')
            return real(user, **kwargs)

        monkeypatch.setattr(reminders.notifications, 'notify', notify)

        assert reminders.deliver_due(utc(2026, 10, 28, 14, 26)) == 1

        assert reminders_of(good)[0].sent_at is not None
        assert reminders_of(bad)[0].sent_at is None  # tried again at the next run

    def test_another_users_reminders_go_to_their_owner(self, funded_user, wednesday):
        other = make_user()
        timed_task(funded_user)
        create_task(other, title='Theirs', due_date=TODAY, due_time=time(14, 30))

        reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        assert Notification.objects.get(user=other).title == 'Theirs'
        assert Notification.objects.get(user=funded_user).title == 'Pay rent'

    def test_a_deleted_notification_leaves_the_history(self, funded_user, wednesday):
        timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        Notification.objects.all().delete()

        assert ReminderDelivery.objects.count() == 1

    def test_a_purged_task_leaves_its_history(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        trash_task(task)
        task.delete()

        delivery = ReminderDelivery.objects.get()
        assert delivery.task_id is None and delivery.task_title == 'Pay rent'


class TestChannelsAndEmail:
    @pytest.fixture
    def sent(self, monkeypatch):
        mails = []

        def fake(**kwargs):
            mails.append(kwargs)
            return 1, 0

        monkeypatch.setattr(reminders, 'send_email', fake)
        return mails

    def make(self, user, channel, **fields):
        task = timed_task(user, **fields)
        Reminder.objects.filter(task=task).update(channel=channel)
        return task

    @staticmethod
    def run_email(reminder: Reminder) -> None:
        """What the queued task does for the reminder's current firing."""
        reminder.refresh_from_db()
        reminders.send_reminder_email.call(str(reminder.pk), str(reminder.fire_token))

    def test_email_is_queued_and_sent_to_a_confirmed_address(
        self, funded_user, wednesday, sent, django_capture_on_commit_callbacks
    ):
        self.make(funded_user, 'email', description='Pay **now**')

        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        [mail] = sent
        assert mail['to'] == funded_user.email
        assert mail['subject'] == 'Reminder: Pay rent'
        assert 'Due 28 Oct 2026, 14:30 (UTC)' in mail['text_body']
        assert 'Pay **now**' in mail['text_body']
        assert '<strong>now</strong>' in mail['html_body']
        assert f'http://localhost:8000{reverse("todo:task", args=[5000])}' not in mail['text_body']
        delivery = ReminderDelivery.objects.get()
        assert (delivery.channel, delivery.note, delivery.notification_id) == ('email', '', None)
        assert not Notification.objects.exists()

    def test_the_email_links_to_the_task(
        self, funded_user, wednesday, sent, settings, django_capture_on_commit_callbacks
    ):
        settings.BASE_URL = 'https://app.example.com/'
        task = self.make(funded_user, 'email')

        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        link = f'https://app.example.com/todo/{task.number}/'
        assert link in sent[0]['text_body'] and link in sent[0]['html_body']

    def test_an_unconfirmed_address_gets_nothing_but_a_history_row(
        self, funded_user, wednesday, sent, django_capture_on_commit_callbacks
    ):
        funded_user.email_confirmed = False
        funded_user.save()
        self.make(funded_user, 'email')

        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        assert sent == []
        delivery = ReminderDelivery.objects.get()
        assert (delivery.channel, delivery.note) == ('email', 'unconfirmed')

    def test_both_does_both(self, funded_user, wednesday, sent, django_capture_on_commit_callbacks):
        self.make(funded_user, 'both')

        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        assert len(sent) == 1 and Notification.objects.count() == 1
        assert sorted(ReminderDelivery.objects.values_list('channel', flat=True)) == [
            'email',
            'in_app',
        ]

    def test_the_text_is_user_text_so_it_is_escaped(
        self, funded_user, wednesday, sent, django_capture_on_commit_callbacks
    ):
        task = self.make(funded_user, 'email', description='<script>alert(1)</script> & more')
        Task.objects.filter(pk=task.pk).update(title='<b>Bold</b> & co')

        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        html = sent[0]['html_body']
        assert '<script>' not in html and '&lt;b&gt;Bold&lt;/b&gt; &amp; co' in html
        assert '<b>Bold</b> & co' in sent[0]['text_body']  # plain text is not HTML

    def test_the_subject_has_only_a_short_title(
        self, funded_user, wednesday, sent, django_capture_on_commit_callbacks
    ):
        task = self.make(funded_user, 'email', description='secret details')
        Task.objects.filter(pk=task.pk).update(title='t' * 200)

        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        subject = sent[0]['subject']
        assert subject == 'Reminder: ' + 't' * 79 + '…'
        assert 'secret' not in subject

    def test_the_email_task_ignores_what_is_gone_or_not_open(self, funded_user, wednesday, sent):
        task = self.make(funded_user, 'email')
        reminder = reminders_of(task)[0]
        reminders.send_reminder_email.call(str(uuid4()), str(uuid4()))
        reminders.send_reminder_email.call('nope', 'nope')
        complete_task(task)

        self.run_email(reminder)

        assert sent == [] and not ReminderDelivery.objects.exists()

    def test_the_email_is_not_sent_twice_for_one_firing(self, funded_user, wednesday, sent):
        task = self.make(funded_user, 'email')
        reminder = reminders_of(task)[0]

        self.run_email(reminder)
        self.run_email(reminder)

        assert len(sent) == 1 and ReminderDelivery.objects.count() == 1

    def test_a_failed_send_is_noted(self, funded_user, wednesday, monkeypatch):
        monkeypatch.setattr(reminders, 'send_email', lambda **kwargs: (0, 1))
        task = self.make(funded_user, 'email')

        self.run_email(reminders_of(task)[0])

        assert ReminderDelivery.objects.get().note == 'failed'

    def test_the_email_shows_the_users_zone(self, funded_user, wednesday, sent):
        funded_user.timezone = 'Europe/Lisbon'
        funded_user.save()
        task = self.make(funded_user, 'email')

        self.run_email(reminders_of(task)[0])

        assert '(Europe/Lisbon)' in sent[0]['text_body']


class TestStaleWindow:
    def test_a_reminder_older_than_the_window_is_marked_sent_without_a_delivery(
        self, funded_user, wednesday
    ):
        task = timed_task(funded_user)  # fires 14:25

        assert reminders.deliver_due(utc(2026, 10, 28, 15, 26)) == 0  # 1 h 1 min late

        [reminder] = reminders_of(task)
        assert reminder.sent_at == utc(2026, 10, 28, 15, 26) and reminder.skipped
        assert not Notification.objects.exists()
        [delivery] = ReminderDelivery.objects.all()  # the history says why nothing came
        assert (delivery.note, delivery.task_id) == ('skipped', task.pk)

    def test_inside_the_window_it_is_still_delivered(self, funded_user, wednesday):
        timed_task(funded_user)

        assert reminders.deliver_due(utc(2026, 10, 28, 15, 20)) == 1

    def test_a_future_reminder_is_left_alone(self, funded_user, wednesday):
        task = timed_task(funded_user)

        reminders.deliver_due(utc(2026, 10, 28, 9, 0))

        assert reminders_of(task)[0].sent_at is None

    def test_a_completed_tasks_old_reminder_is_swept_too_without_a_history_row(
        self, funded_user, wednesday
    ):
        task = timed_task(funded_user)
        complete_task(task)

        reminders.deliver_due(utc(2026, 10, 28, 17, 0))

        [reminder] = reminders_of(task)
        assert reminder.sent_at is not None and reminder.skipped
        assert not ReminderDelivery.objects.exists()  # nothing to miss on a completed task

    def test_an_uncompleted_task_does_not_fire_the_past(self, funded_user, wednesday):
        task = timed_task(funded_user)
        complete_task(task)
        reminders.deliver_due(utc(2026, 10, 28, 17, 0))
        uncomplete_task(task)

        assert reminders.deliver_due(utc(2026, 10, 28, 17, 1)) == 0


class TestJob:
    def test_the_job_is_registered_every_minute(self):
        django_apps.get_app_config('todo')._register_jobs()
        spec = {job.name: job for job in get_jobs()}['todo.send_reminders']

        assert spec.interval == timedelta(minutes=1)
        assert spec.lease == timedelta(minutes=5)
        assert spec.func is jobs.send_reminders

    def test_the_job_delivers_what_is_due(self, funded_user, freeze_now):
        freeze_now(2026, 10, 28, 14, 26)
        timed_task(funded_user)

        jobs.send_reminders()
        jobs.send_reminders()

        assert Notification.objects.count() == 1


class TestAdmin:
    def test_both_admins_are_read_only_and_open(self, funded_user, wednesday):
        timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        funded_user.is_staff = funded_user.is_superuser = True
        funded_user.save()
        client = Client()
        client.force_login(funded_user)

        for model in ('reminder', 'reminderdelivery'):
            assert client.get(f'/admin/todo/{model}/').status_code == 200
            assert client.get(f'/admin/todo/{model}/add/').status_code == 403

    def test_the_reminder_list_shows_the_task(self, funded_user, wednesday):
        task = timed_task(funded_user)
        funded_user.is_staff = funded_user.is_superuser = True
        funded_user.save()
        client = Client()
        client.force_login(funded_user)

        assert f'#{task.number}' in client.get('/admin/todo/reminder/').content.decode()


# -- Pages ---------------------------------------------------------------------------------------


def text(response) -> str:
    return unescape(response.content.decode())


def add_url(task):
    return reverse('todo:reminder-add', args=[task.number])


class TestTaskPage:
    def test_the_page_lists_the_reminders_with_their_times(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.add_reminder(task, minutes_before=1440, channel='email')

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'Reminders' in body
        assert '5 minutes before' in body and '(in-app)' in body
        assert '1 day before' in body and '(email)' in body
        assert 'Wed 28 Oct 2026, 14:25' in body and 'Tue 27 Oct 2026, 14:30' in body

    def test_the_times_follow_the_users_zone(self, web, funded_user, wednesday):
        funded_user.timezone = 'America/Chicago'
        funded_user.save()
        task = timed_task(funded_user)

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'Wed 28 Oct 2026, 14:25' in body  # 14:30 Chicago, 5 minutes before: not 19:25 UTC

    def test_a_task_without_a_date_says_so(self, web, funded_user):
        task = create_task(funded_user, title='x')

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'Set a due date to add reminders.' in body
        assert add_url(task) not in body

    def test_the_add_row_offers_the_presets(self, web, funded_user, wednesday):
        task = timed_task(funded_user)

        body = text(web.get(reverse('todo:task', args=[task.number])))

        for label in ('At time of task', '15 minutes before', '1 week before', 'Custom...'):
            assert label in body
        assert add_url(task) in body

    def test_a_completed_task_lists_them_without_controls(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        complete_task(task)

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert '5 minutes before' in body and add_url(task) not in body

    def test_the_history_block(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        assert 'No reminders sent yet.' in text(web.get(reverse('todo:task', args=[task.number])))

        reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert '28 Oct 2026, 14:26' in body and 'In-app' in body

    def test_a_row_with_reminders_shows_a_bell(self, web, funded_user, wednesday):
        create_task(funded_user, title='With', due_date=TODAY)
        create_task(funded_user, title='Without')

        body = text(web.get(reverse('todo:all')))
        with_row, without_row = body.split('Without')[0], body.split('Without')[1]

        assert 'Has reminders' in with_row and 'Has reminders' not in without_row

    def test_the_details_form_carries_the_reminder_count(self, web, funded_user, wednesday):
        task = timed_task(funded_user)

        assert 'data-reminder-count="1"' in text(web.get(reverse('todo:task', args=[task.number])))


class TestReminderViews:
    def test_add_with_a_preset_answers_the_panel(self, web, funded_user, wednesday):
        task = timed_task(funded_user)

        response = web.post(add_url(task), {'preset': '60', 'channel': 'email'}, **HTMX)

        assert response.status_code == 200
        assert 'id="task-panel"' in response.content.decode()
        assert '1 hour before' in text(response)
        reminder = Reminder.objects.get(task=task, minutes_before=60)
        assert reminder.channel == 'email'

    def test_add_a_custom_time(self, web, funded_user, wednesday):
        task = timed_task(funded_user)

        web.post(add_url(task), {'preset': 'custom', 'amount': '3', 'unit': 'days'}, **HTMX)

        reminder = Reminder.objects.get(task=task, minutes_before=3 * 1440)
        assert reminder.channel == 'in_app'  # the account's default

    def test_add_uses_the_default_channel(self, web, funded_user, wednesday):
        reminders.set_defaults(get_account(funded_user), minutes=5, channel='both')
        task = timed_task(funded_user)

        web.post(add_url(task), {'preset': '15'}, **HTMX)

        assert Reminder.objects.get(task=task, minutes_before=15).channel == 'both'

    @pytest.mark.parametrize(
        ('data', 'message'),
        [
            ({'preset': '5'}, 'There is already a reminder at that time.'),
            ({'preset': 'custom'}, 'Amount: Enter an amount.'),
            ({'preset': 'custom', 'amount': 'x'}, 'Amount: Enter a whole number.'),
            ({'preset': 'custom', 'amount': '31', 'unit': 'days'}, 'between 0 minutes and 30 days'),
            ({'preset': 'bogus'}, 'Reminder: Choose when to remind you.'),
            ({'preset': '15', 'channel': 'sms'}, 'Channel: Choose in-app, email or both.'),
        ],
    )
    def test_refusals_are_a_422_in_the_message_slot(
        self, web, funded_user, wednesday, data, message
    ):
        task = timed_task(funded_user)

        response = web.post(add_url(task), data, **HTMX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#reminder-msg'
        assert message in text(response)

    def test_no_date_is_refused(self, web, funded_user):
        task = create_task(funded_user, title='x')

        response = web.post(add_url(task), {'preset': '5'}, **HTMX)

        assert response.status_code == 422 and 'Set a due date first.' in text(response)

    def test_without_htmx_it_redirects_back(self, web, funded_user, wednesday):
        task = timed_task(funded_user)

        response = web.post(add_url(task), {'preset': '15'})

        assert response.status_code == 302
        assert response['Location'].startswith(reverse('todo:task', args=[task.number]))

    def test_the_edit_budget_applies(self, web, funded_user, wednesday, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'TODO_EDIT_USER': (1, 60)}
        task = timed_task(funded_user)
        web.post(add_url(task), {'preset': '15'}, **HTMX)

        response = web.post(add_url(task), {'preset': '60'}, **HTMX)

        assert response.status_code == 429

    def test_edit(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]
        url = reverse('todo:reminder-edit', args=[task.number, reminder.pk])

        response = web.post(url, {'amount': '2', 'unit': 'hours', 'channel': 'both'}, **HTMX)

        assert response.status_code == 200
        reminder.refresh_from_db()
        assert (reminder.minutes_before, reminder.channel) == (120, 'both')
        assert reminder.fire_at == utc(2026, 10, 28, 12, 30)

    def test_edit_to_a_taken_time_is_a_422(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        other = reminders.add_reminder(task, minutes_before=15, channel='in_app')
        url = reverse('todo:reminder-edit', args=[task.number, other.pk])

        response = web.post(url, {'amount': '5', 'unit': 'minutes'}, **HTMX)

        assert response.status_code == 422 and 'already a reminder' in text(response)

    def test_delete(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]

        response = web.post(
            reverse('todo:reminder-delete', args=[task.number, reminder.pk]), **HTMX
        )

        assert response.status_code == 200 and reminders_of(task) == []
        assert 'No reminders.' in text(response)

    def test_the_views_need_a_login(self, client, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]

        for url in (
            add_url(task),
            reverse('todo:reminder-edit', args=[task.number, reminder.pk]),
            reverse('todo:reminder-delete', args=[task.number, reminder.pk]),
        ):
            assert client.post(url, {'preset': '60'}).status_code == 302
        assert client.get(reverse('todo:reminder-history')).status_code == 302
        assert Reminder.objects.filter(task=task).count() == 1

    def test_another_users_task_and_reminder_are_a_404(self, web, funded_user, wednesday):
        other = make_user()
        theirs = make_task(other, due_date=TODAY, due_time=time(14, 30))  # numbers are per user
        their_reminder = reminders.add_reminder(theirs, minutes_before=5, channel='in_app')
        mine = timed_task(funded_user)

        assert web.post(add_url(theirs), {'preset': '60'}).status_code == 404
        edit = reverse('todo:reminder-edit', args=[theirs.number, their_reminder.pk])
        assert web.post(edit, {'amount': '1', 'unit': 'hours'}).status_code == 404
        delete = reverse('todo:reminder-delete', args=[theirs.number, their_reminder.pk])
        assert web.post(delete).status_code == 404
        # ... nor through one of my own task numbers.
        crossed = reverse('todo:reminder-delete', args=[mine.number, their_reminder.pk])
        assert web.post(crossed).status_code == 404
        assert Reminder.objects.filter(pk=their_reminder.pk).exists()

    def test_a_trashed_task_is_a_404(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        reminder = reminders_of(task)[0]
        trash_task(task)

        assert web.post(add_url(task), {'preset': '60'}).status_code == 404
        delete = reverse('todo:reminder-delete', args=[task.number, reminder.pk])
        assert web.post(delete).status_code == 404

    def test_a_completed_task_is_refused(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        complete_task(task)

        response = web.post(add_url(task), {'preset': '60'}, **HTMX)

        assert response.status_code == 422 and 'Only an open task' in text(response)

    def test_get_is_not_allowed(self, web, funded_user, wednesday):
        task = timed_task(funded_user)

        assert web.get(add_url(task)).status_code == 405


class TestHistoryPage:
    def test_it_lists_the_users_deliveries_newest_first(self, web, funded_user, wednesday):
        first = timed_task(funded_user)
        second = create_task(funded_user, title='Later', due_date=TODAY, due_time=time(16, 0))
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        reminders.deliver_due(utc(2026, 10, 28, 15, 56))

        response = web.get(reverse('todo:reminder-history'))
        body = text(response)

        assert response.status_code == 200
        assert body.index('Later') < body.index('Pay rent')
        assert f'#{first.number}' in body and f'#{second.number}' in body
        assert reverse('todo:task', args=[first.number]) in body

    def test_a_purged_task_says_so(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        trash_task(task)
        task.delete()

        body = text(web.get(reverse('todo:reminder-history')))

        assert 'Pay rent' in body and '(task deleted)' in body

    def test_an_unconfirmed_email_is_explained(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        ReminderDelivery.objects.create(
            task=task,
            user=funded_user,
            task_number=task.number,
            task_title=task.title,
            channel='email',
            note='unconfirmed',
            sent_at=wednesday_noon(),
            fire_at=wednesday_noon(),
        )

        assert 'email address is not confirmed' in text(web.get(reverse('todo:reminder-history')))

    def test_it_does_not_show_other_users_deliveries(self, web, funded_user, wednesday):
        other = make_user()
        create_task(other, title='Theirs', due_date=TODAY, due_time=time(14, 30))
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        assert 'Theirs' not in text(web.get(reverse('todo:reminder-history')))

    def test_fifty_to_a_page(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        ReminderDelivery.objects.bulk_create(
            ReminderDelivery(
                task=task,
                user=funded_user,
                task_number=task.number,
                task_title=f'n{index}',
                channel='in_app',
                sent_at=wednesday_noon() - timedelta(minutes=index),
                fire_at=wednesday_noon(),
            )
            for index in range(60)
        )

        first = text(web.get(reverse('todo:reminder-history')))
        second = text(web.get(reverse('todo:reminder-history'), {'page': 2}))

        assert 'n0<' in first and 'n49<' in first and 'n50<' not in first
        assert 'n50<' in second and 'Page 2 of 2' in second

    def test_the_empty_page(self, web):
        assert 'No reminders have been delivered yet.' in text(
            web.get(reverse('todo:reminder-history'))
        )

    def test_it_is_linked_from_the_settings(self, web):
        assert reverse('todo:reminder-history') in text(web.get(reverse('todo:settings')))


class TestSettings:
    def post(self, web, **data):
        return web.post(reverse('todo:settings'), {'default_due_time': '09:00', **data}, **HTMX)

    def test_the_form_shows_the_defaults(self, web):
        body = text(web.get(reverse('todo:settings')))

        assert 'Default reminder' in body and 'Default channel' in body
        assert 'Email reminders go to your confirmed address only' in body
        assert 'value="5"' in body

    def test_set_an_amount_unit_and_channel(self, web, funded_user):
        response = self.post(
            web,
            default_reminder_mode='set',
            default_reminder_amount='2',
            default_reminder_unit='hours',
            default_reminder_channel='both',
        )

        assert response.status_code == 200
        account = get_account(funded_user)
        account.refresh_from_db()
        assert (account.default_reminder_minutes, account.default_reminder_channel) == (120, 'both')

    def test_none_turns_it_off(self, web, funded_user):
        self.post(web, default_reminder_mode='none', default_reminder_channel='email')

        account = get_account(funded_user)
        account.refresh_from_db()
        assert account.default_reminder_minutes is None
        assert account.default_reminder_channel == 'email'
        assert reminders_of(create_task(funded_user, title='x', due_date=TODAY)) == []

    def test_the_form_shows_none_as_selected(self, web, funded_user):
        reminders.set_defaults(get_account(funded_user), minutes=None, channel='in_app')

        body = text(web.get(reverse('todo:settings')))

        assert '<option value="none" selected>' in body

    def test_a_form_without_the_reminder_fields_leaves_them(self, web, funded_user):
        reminders.set_defaults(get_account(funded_user), minutes=30, channel='email')

        assert self.post(web).status_code == 200

        account = get_account(funded_user)
        account.refresh_from_db()
        assert (account.default_reminder_minutes, account.default_reminder_channel) == (30, 'email')

    @pytest.mark.parametrize(
        ('data', 'message'),
        [
            ({'default_reminder_mode': 'set'}, 'Default reminder: Enter an amount.'),
            (
                {'default_reminder_mode': 'set', 'default_reminder_amount': '-1'},
                'Enter 0 or more.',
            ),
            (
                {
                    'default_reminder_mode': 'set',
                    'default_reminder_amount': '99',
                    'default_reminder_unit': 'days',
                },
                'between 0 minutes and 30 days',
            ),
            ({'default_reminder_mode': 'maybe'}, 'Choose whether new tasks get a reminder.'),
            (
                {'default_reminder_mode': 'none', 'default_reminder_channel': 'sms'},
                'Default channel: Choose in-app, email or both.',
            ),
        ],
    )
    def test_refusals(self, web, funded_user, data, message):
        response = self.post(web, **data)

        assert response.status_code == 422
        assert message in text(response)
        account = get_account(funded_user)
        account.refresh_from_db()
        assert account.default_reminder_minutes == 5

    def test_a_refusal_saves_nothing_at_all(self, web, funded_user):
        response = self.post(
            web,
            default_due_time='07:00',
            default_reminder_mode='set',
            default_reminder_amount='99',
            default_reminder_unit='days',
        )

        assert response.status_code == 422
        account = get_account(funded_user)
        account.refresh_from_db()
        assert account.default_due_time == time(9, 0)

    def test_a_new_default_due_time_reschedules(
        self, web, funded_user, wednesday, django_capture_on_commit_callbacks
    ):
        task = create_task(funded_user, title='x', due_date=TODAY)

        with django_capture_on_commit_callbacks(execute=True):
            self.post(web, default_due_time='07:00')

        assert reminders_of(task)[0].fire_at == utc(2026, 10, 28, 6, 55)


class TestRearming:
    """A due date the user changes arms an already sent reminder for the new date; the zone and the
    default due time never do."""

    def test_postponing_after_delivery_reminds_again(self, funded_user, wednesday):
        task = timed_task(funded_user)
        assert reminders.deliver_due(utc(2026, 10, 28, 14, 26)) == 1
        assert reminders_of(task)[0].sent_at is not None

        postpone_task(task, preset='tomorrow')

        [reminder] = reminders_of(task)
        assert reminder.sent_at is None and reminder.fire_at == utc(2026, 10, 29, 14, 25)
        assert reminders.deliver_due(utc(2026, 10, 29, 14, 26)) == 1
        assert Notification.objects.filter(user=funded_user).count() == 2

    def test_rescheduling_all_overdue_to_today_reminds_again(self, funded_user, wednesday):
        task = create_task(
            funded_user, title='x', due_date=date(2026, 10, 27), due_time=time(23, 0)
        )
        assert reminders.deliver_due(utc(2026, 10, 27, 22, 56)) == 1

        assert reschedule_overdue(funded_user) == 1

        [reminder] = reminders_of(task)
        assert reminder.sent_at is None and reminder.fire_at == utc(2026, 10, 28, 22, 55)
        assert reminders.deliver_due(utc(2026, 10, 28, 22, 56)) == 1
        assert Notification.objects.filter(user=funded_user).count() == 2

    @pytest.mark.parametrize(
        'changes',
        [{'due_date': date(2026, 10, 30)}, {'due_time': time(18, 0)}],
        ids=['date', 'time'],
    )
    def test_editing_the_date_or_time_reminds_again(self, funded_user, wednesday, changes):
        task = timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        update_task(task, changes=changes)

        [reminder] = reminders_of(task)
        assert reminder.sent_at is None and not reminder.skipped

    def test_a_skipped_reminder_is_armed_again_too(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 16, 0))  # swept: more than an hour late
        assert reminders_of(task)[0].skipped
        Reminder.objects.filter(task=task).update(failures=2)

        postpone_task(task, preset='tomorrow')

        [reminder] = reminders_of(task)
        assert (reminder.sent_at, reminder.skipped, reminder.failures) == (None, False, 0)

    def test_a_new_time_that_is_not_ahead_leaves_it_sent(self, funded_user, wednesday):
        task = timed_task(funded_user, hour=20, minute=0)  # fires 19:55
        reminders.deliver_due(utc(2026, 10, 28, 19, 56))

        update_task(task, changes={'due_time': time(8, 0)})  # 07:55 today: before now (12:00)

        [reminder] = reminders_of(task)
        assert reminder.sent_at is not None and reminder.fire_at == utc(2026, 10, 28, 7, 55)

    def test_a_time_zone_change_never_arms_a_sent_reminder(self, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        funded_user.timezone = 'Asia/Tokyo'  # 14:30 Tokyo is hours earlier in UTC: still sent
        reminders.reschedule_user(funded_user)
        funded_user.timezone = 'America/Los_Angeles'  # 14:30 there is later: a future instant

        reminders.reschedule_user(funded_user)

        [reminder] = reminders_of(task)
        assert reminder.sent_at is not None and reminder.fire_at == utc(2026, 10, 28, 14, 25)

    def test_a_default_due_time_change_never_arms_a_sent_reminder(self, funded_user, wednesday):
        task = create_task(funded_user, title='x', due_date=TODAY)  # due 09:00, fires 08:55
        reminders.deliver_due(utc(2026, 10, 28, 9, 0))

        reminders.reschedule_user_reminders.call(str(funded_user.pk), True)
        account = get_account(funded_user)
        account.default_due_time = time(18, 0)
        account.save()
        reminders.reschedule_user_reminders.call(str(funded_user.pk), True)

        [reminder] = reminders_of(task)
        assert reminder.sent_at is not None and reminder.fire_at == utc(2026, 10, 28, 8, 55)

    def test_rescheduling_all_overdue_uses_a_fixed_number_of_queries(self, funded_user, wednesday):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def overdue_tasks(count):
            for i in range(count):
                task = create_task(
                    funded_user,
                    title=f'x{i}',
                    due_date=date(2026, 10, 20),
                    due_time=time(9, i),
                )
                reminders.add_reminder(task, minutes_before=60, channel='email')

        overdue_tasks(2)
        with CaptureQueriesContext(connection) as small:
            assert reschedule_overdue(funded_user) == 2

        Task.objects.filter(user=funded_user).update(due_date=date(2026, 10, 20))
        overdue_tasks(10)
        with CaptureQueriesContext(connection) as large:
            assert reschedule_overdue(funded_user) == 12

        assert len(large) == len(small)
        assert all(r.fire_at.date() == TODAY for r in Reminder.objects.filter(minutes_before=60))


class TestDefaultDueTimeQueued:
    def test_the_request_does_not_reschedule_a_task_does(
        self, funded_user, wednesday, django_capture_on_commit_callbacks
    ):
        task = create_task(funded_user, title='x', due_date=TODAY)

        with django_capture_on_commit_callbacks() as callbacks:
            update_default_due_time(get_account(funded_user), time(7, 0))

        assert len(callbacks) == 1
        assert reminders_of(task)[0].fire_at == utc(2026, 10, 28, 8, 55)  # not yet
        callbacks[0]()
        assert reminders_of(task)[0].fire_at == utc(2026, 10, 28, 6, 55)

    def test_only_date_only_tasks_are_recomputed(self, funded_user, wednesday):
        timed = timed_task(funded_user)
        date_only = create_task(funded_user, title='x', due_date=TODAY)
        account = get_account(funded_user)
        account.default_due_time = time(7, 0)
        account.save()

        reminders.reschedule_user_reminders.call(str(funded_user.pk), True)

        assert reminders_of(timed)[0].fire_at == utc(2026, 10, 28, 14, 25)
        assert reminders_of(date_only)[0].fire_at == utc(2026, 10, 28, 6, 55)


class TestEmailFiring:
    @pytest.fixture
    def sent(self, monkeypatch):
        mails = []

        def fake(**kwargs):
            mails.append(kwargs)
            return 1, 0

        monkeypatch.setattr(reminders, 'send_email', fake)
        return mails

    def email_task(self, user):
        task = timed_task(user)
        Reminder.objects.filter(task=task).update(channel='email')
        return task

    def test_a_reminder_armed_again_emails_again_even_at_the_same_instant(
        self, funded_user, wednesday, sent, django_capture_on_commit_callbacks
    ):
        task = self.email_task(funded_user)
        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        assert len(sent) == 1
        reminder = reminders_of(task)[0]
        # 5 -> 10 -> 5 minutes before: the same instant again, armed twice.
        reminders.update_reminder(reminder, minutes_before=10)
        reminders.update_reminder(reminder, minutes_before=5)
        assert reminder.fire_at == utc(2026, 10, 28, 14, 25) and reminder.sent_at is None

        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 27))

        assert len(sent) == 2
        assert ReminderDelivery.objects.filter(channel='email').count() == 2

    def test_a_queued_email_of_an_older_firing_sends_nothing(self, funded_user, wednesday, sent):
        task = self.email_task(funded_user)
        reminder = reminders_of(task)[0]
        old = str(reminder.fire_token)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))  # a new firing: the token changed
        reminder.refresh_from_db()
        assert str(reminder.fire_token) != old

        reminders.send_reminder_email.call(str(reminder.pk), old)

        assert sent == []

    def test_each_claim_has_its_own_token(self, funded_user, wednesday):
        task = self.email_task(funded_user)
        first = reminders_of(task)[0].fire_token

        reminders.deliver_due(utc(2026, 10, 28, 14, 26))
        second = reminders_of(task)[0].fire_token
        postpone_task(task, preset='tomorrow')
        reminders.deliver_due(utc(2026, 10, 29, 14, 26))

        assert len({first, second, reminders_of(task)[0].fire_token}) == 3

    def test_a_failure_to_queue_the_email_is_recorded(
        self, funded_user, wednesday, monkeypatch, django_capture_on_commit_callbacks
    ):
        class Broken:
            @staticmethod
            def enqueue(*args):
                raise RuntimeError('queue down')

        monkeypatch.setattr(reminders, 'send_reminder_email', Broken)
        task = self.email_task(funded_user)

        with django_capture_on_commit_callbacks(execute=True):
            assert reminders.deliver_due(utc(2026, 10, 28, 14, 26)) == 1

        delivery = ReminderDelivery.objects.get()
        assert (delivery.channel, delivery.note, delivery.task_id) == ('email', 'failed', task.pk)
        assert reminders_of(task)[0].sent_at is not None

    def test_the_footer_says_what_the_user_can_do(
        self, funded_user, wednesday, sent, settings, django_capture_on_commit_callbacks
    ):
        settings.BASE_URL = 'https://app.example.com/'
        task = self.email_task(funded_user)

        with django_capture_on_commit_callbacks(execute=True):
            reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        [mail] = sent
        task_url = f'https://app.example.com/todo/{task.number}/'
        settings_url = 'https://app.example.com' + reverse('todo:settings')
        assert 'You set this reminder on "Pay rent"' in mail['text_body']
        assert 'Change or remove it on the task page' in mail['text_body']
        assert 'your default for new tasks is in To-do settings' in mail['text_body']
        assert task_url in mail['text_body'] and settings_url in mail['text_body']
        assert 'You set this reminder on' in mail['html_body']
        assert f'href="{settings_url}"' in mail['html_body']
        assert f'href="{task_url}"' in mail['html_body']
        assert 'turn email reminders off' not in mail['text_body']


class TestFailureLogging:
    def test_only_the_first_failure_logs_a_traceback(
        self, funded_user, wednesday, monkeypatch, caplog
    ):
        task = timed_task(funded_user)

        def boom(user, **kwargs):
            raise RuntimeError('boom')

        monkeypatch.setattr(reminders.notifications, 'notify', boom)

        for minute in (26, 27, 28):
            reminders.deliver_due(utc(2026, 10, 28, 14, minute))

        ours = [r for r in caplog.records if r.name == reminders.logger.name]
        assert [r.levelname for r in ours] == ['ERROR', 'WARNING', 'WARNING']
        assert ours[0].exc_info is not None
        assert all(r.exc_info is None for r in ours[1:])
        assert 'RuntimeError: boom' in ours[1].getMessage()
        assert reminders_of(task)[0].failures == 3
        assert reminders_of(task)[0].sent_at is None

    def test_the_sweep_still_ends_it_an_hour_later(self, funded_user, wednesday, monkeypatch):
        task = timed_task(funded_user)
        monkeypatch.setattr(
            reminders.notifications, 'notify', lambda *a, **k: (_ for _ in ()).throw(OSError())
        )
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        reminders.deliver_due(utc(2026, 10, 28, 15, 26))

        [reminder] = reminders_of(task)
        assert reminder.sent_at is not None and reminder.skipped

    def test_editing_the_time_resets_the_count(self, funded_user, wednesday):
        task = timed_task(funded_user)
        Reminder.objects.filter(task=task).update(failures=4)

        reminders.update_reminder(reminders_of(task)[0], minutes_before=10)

        assert reminders_of(task)[0].failures == 0


class TestSkipped:
    def test_the_panel_says_skipped_not_sent(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 16, 0))  # swept

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'Skipped Wed 28 Oct 2026, 14:25' in body
        assert 'Sent Wed 28 Oct' not in body
        assert 'skipped: its time had already passed' in body  # in the task's history too

    def test_a_delivered_one_still_says_sent(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 14, 26))

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'Sent Wed 28 Oct 2026, 14:25' in body and 'Skipped' not in body

    def test_the_history_page_says_skipped(self, web, funded_user, wednesday):
        timed_task(funded_user)
        reminders.deliver_due(utc(2026, 10, 28, 16, 0))

        body = text(web.get(reverse('todo:reminder-history')))

        assert 'skipped: its time had already passed' in body

    def test_a_late_added_reminder_is_skipped_in_the_panel(self, web, funded_user, wednesday):
        task = timed_task(funded_user)
        reminders.add_reminder(task, minutes_before=2 * 1440, channel='in_app')

        body = text(web.get(reverse('todo:task', args=[task.number])))

        assert 'Skipped Mon 26 Oct 2026, 14:30' in body

    def test_an_inactive_owners_reminder_is_swept_without_a_history_row(
        self, funded_user, wednesday
    ):
        task = timed_task(funded_user)
        funded_user.is_active = False
        funded_user.save()

        reminders.deliver_due(utc(2026, 10, 28, 16, 0))

        assert reminders_of(task)[0].skipped and not ReminderDelivery.objects.exists()


class TestIndex:
    def test_one_partial_index_on_unsent_reminders(self):
        meta = Reminder._meta  # ty: ignore[unresolved-attribute]
        [index] = meta.indexes

        assert index.fields == ['fire_at']
        assert index.condition is not None and 'sent_at' in str(index.condition)
        assert not meta.get_field('fire_at').db_index

    def test_the_migration_creates_it(self):
        from importlib import import_module

        migration = import_module('apps.todo.migrations.0005_reminders').Migration

        assert migration.dependencies[0] == ('todo', '0004_attachments_and_storage_billing')
        added = [
            op.index
            for op in migration.operations
            if op.__class__.__name__ == 'AddIndex' and op.model_name == 'reminder'
        ]
        assert [(i.name, i.fields) for i in added] == [('todo_reminder_unsent_fire', ['fire_at'])]
        assert str(added[0].condition) == "(AND: ('sent_at__isnull', True))"
