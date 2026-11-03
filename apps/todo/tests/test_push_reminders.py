"""Reminders pushed to a phone with Complete and Snooze (issue #239 follow-up): an in-app reminder
also goes to the owner's devices, the push is recorded with its id, a tap on a button completes
the task or snoozes the reminder and answers with a confirmation, a snooze survives the passes that
only move the same due moment, and the "Snooze for" setting on the page, the API and the CLI."""

import json
from datetime import date, time
from unittest import mock

import pytest
from django.urls import reverse

from apps.accounts.services.timezones import set_timezone
from apps.core.models import Notification, PushAction
from apps.core.services import push
from apps.core.tests.test_push import FCM, PRIVATE_KEY, PUBLIC_KEY, subscription_json

from ..models import DeliveryChannel, Reminder, ReminderDelivery, Task
from ..services import (
    create_task,
    get_account,
    postpone_task,
    reminders,
    trash_task,
    update_default_due_time,
)
from .api_support import auth, get, patch  # noqa: F401 -- `auth` is a fixture
from .factories import make_task, utc

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 28)  # `wednesday` freezes now at 12:00 UTC
FIRES = utc(2026, 10, 28, 14, 26)  # the default 5-minute reminder of a task due at 14:30


@pytest.fixture
def configured(settings):
    settings.VAPID_PUBLIC_KEY = PUBLIC_KEY
    settings.VAPID_PRIVATE_KEY = PRIVATE_KEY
    settings.VAPID_SUBJECT = 'mailto:push@example.com'
    return settings


@pytest.fixture
def webpush():
    with mock.patch('pywebpush.webpush') as fake:
        yield fake


@pytest.fixture
def phone(funded_user):
    return push.save_subscription(funded_user, subscription_json(FCM + 'phone'))


def due_task(user, **fields) -> Task:
    return create_task(user, title='Pay rent', due_date=TODAY, due_time=time(14, 30), **fields)


def deliver(django_capture_on_commit_callbacks, now=FIRES) -> int:
    with django_capture_on_commit_callbacks(execute=True):
        return reminders.deliver_due(now)


def sent_payload(webpush) -> dict:
    return json.loads(webpush.call_args.kwargs['data'])


def tap(client, payload: dict, action: str):
    data = payload['data']
    return client.post(
        data['action_url'],
        data=json.dumps({'token': data['action_token'], 'action': action}),
        content_type='application/json',
    )


class TestPushedWithTheReminder:
    def test_an_in_app_reminder_is_pushed_with_complete_and_snooze(
        self, funded_user, wednesday, configured, phone, webpush, django_capture_on_commit_callbacks
    ):
        task = due_task(funded_user)

        assert deliver(django_capture_on_commit_callbacks) == 1

        payload = sent_payload(webpush)
        assert payload['title'] == 'Pay rent'
        assert payload['body'] == 'Due 28 Oct 2026, 14:30'
        assert payload['tag'] == f'todo-task-{task.pk}'
        assert payload['data']['url'] == reverse('todo:task', args=[task.number])
        assert payload['actions'] == [
            {'action': 'complete', 'title': 'Complete'},
            {'action': 'snooze', 'title': 'Snooze 15 min'},
        ]
        push_row = ReminderDelivery.objects.get(channel=DeliveryChannel.PUSH)
        assert str(push_row.push_id) == payload['data']['push_id']
        assert push_row.task == task and push_row.user == funded_user
        # The in-app notice is still there, next to it.
        assert Notification.objects.filter(user=funded_user).count() == 1
        assert ReminderDelivery.objects.filter(channel=DeliveryChannel.IN_APP).count() == 1

    def test_the_snooze_button_says_the_users_setting(
        self, funded_user, wednesday, configured, phone, webpush, django_capture_on_commit_callbacks
    ):
        reminders.set_snooze(get_account(funded_user), 480)
        due_task(funded_user)

        deliver(django_capture_on_commit_callbacks)

        assert sent_payload(webpush)['actions'][1]['title'] == 'Snooze 8 h'

    def test_both_pushes_too(
        self, funded_user, wednesday, configured, phone, webpush, django_capture_on_commit_callbacks
    ):
        task = make_task(funded_user, due_date=TODAY, due_time=time(14, 30))
        reminders.add_reminder(task, minutes_before=5, channel='both')

        deliver(django_capture_on_commit_callbacks)

        assert webpush.call_count == 1

    def test_an_email_only_reminder_is_not_pushed(
        self, funded_user, wednesday, configured, phone, webpush, django_capture_on_commit_callbacks
    ):
        task = make_task(funded_user, due_date=TODAY, due_time=time(14, 30))
        reminders.add_reminder(task, minutes_before=5, channel='email')

        deliver(django_capture_on_commit_callbacks)

        assert not webpush.called
        assert not ReminderDelivery.objects.filter(channel=DeliveryChannel.PUSH).exists()

    def test_nothing_is_pushed_without_a_device(
        self, funded_user, wednesday, configured, webpush, django_capture_on_commit_callbacks
    ):
        due_task(funded_user)

        assert deliver(django_capture_on_commit_callbacks) == 1

        assert not webpush.called
        assert not ReminderDelivery.objects.filter(channel=DeliveryChannel.PUSH).exists()

    def test_nothing_is_pushed_while_push_is_off(
        self, funded_user, wednesday, phone, webpush, django_capture_on_commit_callbacks, settings
    ):
        settings.VAPID_PRIVATE_KEY = ''
        due_task(funded_user)

        assert deliver(django_capture_on_commit_callbacks) == 1

        assert not webpush.called
        assert not ReminderDelivery.objects.filter(channel=DeliveryChannel.PUSH).exists()

    def test_the_history_page_lists_the_push(
        self, web, funded_user, wednesday, configured, phone, webpush,
        django_capture_on_commit_callbacks,
    ):  # fmt: skip
        due_task(funded_user)
        deliver(django_capture_on_commit_callbacks)

        html = web.get(reverse('todo:reminder-history')).content.decode()

        assert 'Push' in html


class TestAnswers:
    @pytest.fixture
    def payload(
        self, funded_user, wednesday, configured, phone, webpush, django_capture_on_commit_callbacks
    ):
        self.task = due_task(funded_user)
        deliver(django_capture_on_commit_callbacks)
        return sent_payload(webpush)

    def test_complete_completes_the_task(self, client, payload):
        response = tap(client, payload, 'complete')

        assert response.status_code == 200 and response.json() == {'message': 'Completed.'}
        self.task.refresh_from_db()
        assert self.task.completed_at is not None
        answer = PushAction.objects.get()
        assert (answer.kind, answer.action) == ('todo.reminder', 'complete')

    def test_complete_says_how_many_open_subtasks_went_with_it(self, client, payload, funded_user):
        create_task(funded_user, title='Find the account number', parent=self.task)

        response = tap(client, payload, 'complete')

        assert response.json() == {'message': 'Completed, with 1 open subtask.'}
        assert not Task.objects.filter(parent=self.task, completed_at__isnull=True).exists()

    def test_an_already_completed_task_says_so(self, client, payload):
        Task.objects.filter(pk=self.task.pk).update(completed_at=utc(2026, 10, 28, 14, 0))

        assert tap(client, payload, 'complete').json() == {'message': 'Already completed.'}

    def test_a_trashed_task_is_left_alone(self, client, payload):
        trash_task(self.task)

        response = tap(client, payload, 'snooze')

        assert response.json() == {'message': 'This task is in the Trash.'}
        self.task.refresh_from_db()
        assert self.task.completed_at is None

    def test_a_task_deleted_for_good(self, client, payload):
        Task.objects.filter(pk=self.task.pk).delete()

        assert tap(client, payload, 'complete').json() == {'message': 'This task no longer exists.'}

    def test_snooze_arms_the_reminder_again(self, client, payload, funded_user):
        response = tap(client, payload, 'snooze')

        # `wednesday` freezes now at 12:00 UTC, the user has no zone (UTC): 15 minutes later.
        assert response.json() == {'message': 'Snoozed until 12:15.'}
        reminder = Reminder.objects.get(task=self.task)
        assert reminder.fire_at == utc(2026, 10, 28, 12, 15)
        assert reminder.snoozed_until == reminder.fire_at
        assert reminder.sent_at is None and not reminder.skipped

    def test_a_snooze_into_another_day_names_the_day(self, client, payload, funded_user):
        reminders.set_snooze(get_account(funded_user), 1440)

        assert tap(client, payload, 'snooze').json() == {
            'message': 'Snoozed until Thu 29 Oct, 12:00.'
        }

    def test_the_snooze_time_is_on_the_users_clock(self, client, payload, funded_user):
        set_timezone(funded_user, 'America/Chicago')  # UTC-5 until 1 November

        assert tap(client, payload, 'snooze').json() == {'message': 'Snoozed until 07:15.'}

    def test_a_removed_reminder_cannot_be_snoozed(self, client, payload):
        Reminder.objects.filter(task=self.task).delete()

        assert tap(client, payload, 'snooze').json() == {'message': 'This reminder was removed.'}

    def test_a_second_tap_changes_nothing(self, client, payload):
        tap(client, payload, 'snooze')
        fire_at = Reminder.objects.get(task=self.task).fire_at

        assert tap(client, payload, 'complete').status_code == 204

        self.task.refresh_from_db()
        assert self.task.completed_at is None
        assert Reminder.objects.get(task=self.task).fire_at == fire_at

    def test_another_users_reminder_is_not_found(self, client, payload, other_user):
        ReminderDelivery.objects.filter(channel='push').update(user=other_user)

        assert tap(client, payload, 'complete').json() == {'message': 'This task no longer exists.'}


class TestSnoozedReminder:
    def snoozed(self, user) -> Reminder:
        task = due_task(user)
        reminder = Reminder.objects.get(task=task)
        reminders.snooze(reminder)  # now (12:00 UTC) + 15 minutes
        reminder.refresh_from_db()
        return reminder

    def test_it_fires_at_the_snooze_and_forgets_it(
        self, funded_user, wednesday, django_capture_on_commit_callbacks
    ):
        reminder = self.snoozed(funded_user)

        assert deliver(django_capture_on_commit_callbacks, utc(2026, 10, 28, 12, 15)) == 1

        reminder.refresh_from_db()
        assert reminder.sent_at is not None and reminder.snoozed_until is None

    def test_a_time_zone_change_leaves_it_alone(self, funded_user, wednesday):
        reminder = self.snoozed(funded_user)

        reminders.reschedule_user(funded_user)

        reminder.refresh_from_db()
        assert reminder.fire_at == utc(2026, 10, 28, 12, 15) and reminder.snoozed_until

    def test_a_new_default_due_time_leaves_it_alone(self, funded_user, wednesday):
        task = create_task(funded_user, title='Rent', due_date=TODAY)
        reminder = Reminder.objects.get(task=task)
        reminders.snooze(reminder)

        update_default_due_time(get_account(funded_user), time(8, 0))
        reminders.reschedule_user(funded_user, untimed_only=True)

        reminder.refresh_from_db()
        assert reminder.fire_at == utc(2026, 10, 28, 12, 15)

    def test_a_new_due_date_replaces_the_snooze(self, funded_user, wednesday):
        reminder = self.snoozed(funded_user)

        postpone_task(reminder.task, preset='tomorrow')

        reminder.refresh_from_db()
        assert reminder.snoozed_until is None
        assert reminder.fire_at == utc(2026, 10, 29, 14, 25)

    def test_editing_its_time_replaces_the_snooze(self, funded_user, wednesday):
        reminder = self.snoozed(funded_user)

        reminders.update_reminder(reminder, minutes_before=60)

        reminder.refresh_from_db()
        assert reminder.snoozed_until is None and reminder.fire_at == utc(2026, 10, 28, 13, 30)

    def test_the_task_page_says_snoozed(self, web, funded_user, wednesday):
        reminder = self.snoozed(funded_user)

        html = web.get(reverse('todo:task', args=[reminder.task.number])).content.decode()

        assert 'Snoozed until Wed 28 Oct 2026, 12:15' in html

    def test_a_completed_task_cannot_be_snoozed(self, funded_user, wednesday):
        task = due_task(funded_user)
        Task.objects.filter(pk=task.pk).update(completed_at=utc(2026, 10, 28, 11, 0))
        reminder = Reminder.objects.get(task=task)
        reminder.task.refresh_from_db()

        with pytest.raises(Exception, match='Only an open task has reminders'):
            reminders.snooze(reminder)


class TestSnoozeSetting:
    def test_the_default_is_15_minutes(self, funded_user):
        assert get_account(funded_user).snooze_minutes == 15

    def test_only_the_four_choices(self, funded_user):
        with pytest.raises(Exception, match='Choose 15 minutes, 1 hour, 8 hours or 1 day.'):
            reminders.set_snooze(get_account(funded_user), 30)

    def test_the_settings_page_saves_it(self, web, funded_user):
        response = web.post(
            reverse('todo:settings'), {'default_due_time': '09:00', 'snooze_minutes': '60'}
        )

        assert response.status_code == 302
        assert get_account(funded_user).snooze_minutes == 60

    def test_the_settings_page_shows_it(self, web, funded_user):
        reminders.set_snooze(get_account(funded_user), 1440)

        html = web.get(reverse('todo:settings')).content.decode()

        assert 'Snooze for' in html and '<option value="1440" selected>1 day</option>' in html

    def test_a_bad_value_is_a_422(self, web, funded_user):
        response = web.post(
            reverse('todo:settings'),
            {'default_due_time': '09:00', 'snooze_minutes': '30'},
            HTTP_HX_REQUEST='true',
        )

        assert response.status_code == 422
        assert 'Choose 15 minutes, 1 hour, 8 hours or 1 day.' in response.content.decode()
        assert get_account(funded_user).snooze_minutes == 15

    def test_leaving_it_out_keeps_it(self, web, funded_user):
        reminders.set_snooze(get_account(funded_user), 480)

        web.post(reverse('todo:settings'), {'default_due_time': '10:00'})

        assert get_account(funded_user).snooze_minutes == 480


class TestSnoozeApi:
    def test_get_and_patch(self, client, auth, funded_user):
        assert get(client, '/settings', auth).json()['snooze_minutes'] == 15

        body = patch(client, '/settings', auth, {'snooze_minutes': 60}).json()

        assert body['snooze_minutes'] == 60
        assert get_account(funded_user).snooze_minutes == 60

    def test_a_bad_value_is_a_400(self, client, auth, funded_user):
        response = patch(client, '/settings', auth, {'snooze_minutes': 30})

        assert response.status_code == 400
        assert get_account(funded_user).snooze_minutes == 15

    def test_a_reminder_says_when_it_is_snoozed(self, client, auth, funded_user, wednesday):
        task = due_task(funded_user)
        reminders.snooze(Reminder.objects.get(task=task))

        body = get(client, f'/tasks/{task.number}', auth).json()

        assert body['reminders'][0]['snoozed_until'].startswith('2026-10-28T12:15')
