"""Web Push answers a product handles (the push reminders of To-do): the handler registry, the
confirmation `record_action` hands back, a failing handler rolling the answer back, the view's
200 / 204 / 500, and a push id the caller knows before the push is sent (`queue_push`)."""

import json
import uuid
from unittest import mock

import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory
from apps.core.models import PushAction
from apps.core.services import push

from .test_push import FCM, PRIVATE_KEY, PUBLIC_KEY, subscription_json

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

KIND = 'tests.answer'
ACTIONS = ({'action': 'done', 'title': 'Done'}, {'action': 'later', 'title': 'Later'})


@pytest.fixture
def configured(settings):
    settings.VAPID_PUBLIC_KEY = PUBLIC_KEY
    settings.VAPID_PRIVATE_KEY = PRIVATE_KEY
    settings.VAPID_SUBJECT = 'mailto:push@example.com'
    return settings


@pytest.fixture
def user(db):
    return UserFactory(email_confirmed=True)


@pytest.fixture
def handlers(monkeypatch):
    """A clean registry for the test (To-do's handler stays registered outside it)."""
    registry: dict = {}
    monkeypatch.setattr(push, '_action_handlers', registry)
    return registry


def token_for(user, actions=ACTIONS, kind=KIND, push_id=None) -> tuple[str, uuid.UUID]:
    push_id = push_id or uuid.uuid4()
    token = push.make_action_token(
        user.pk, push_id, kind, [a['action'] for a in actions], push.password_fingerprint(user)
    )
    return token, push_id


class TestRegistry:
    def test_registering_the_same_handler_again_is_harmless(self, handlers):
        def handler(answer):
            return 'ok'

        push.register_action_handler(KIND, handler)
        push.register_action_handler(KIND, handler)

        assert handlers == {KIND: handler}

    def test_a_second_handler_for_a_kind_is_refused(self, handlers):
        push.register_action_handler(KIND, lambda answer: 'one')

        with pytest.raises(ValueError, match='already registered'):
            push.register_action_handler(KIND, lambda answer: 'two')

    def test_the_todo_app_registers_its_reminder_handler(self):
        from apps.todo.services import reminders

        assert push._action_handlers[reminders.PUSH_KIND] is reminders.answer_push


class TestRecordAction:
    def test_the_handler_runs_and_its_text_comes_back(self, user, handlers):
        seen = []
        push.register_action_handler(KIND, lambda answer: seen.append(answer) or 'Done it.')
        token, push_id = token_for(user)

        answer = push.record_action(token, 'done')

        assert answer.confirmation == 'Done it.'
        assert seen == [answer] and seen[0].push_id == push_id and seen[0].action == 'done'

    def test_a_kind_without_a_handler_has_no_confirmation(self, user, handlers):
        token, _ = token_for(user)

        assert push.record_action(token, 'done').confirmation is None

    def test_a_repeat_does_not_run_the_handler_again(self, user, handlers):
        calls = []
        push.register_action_handler(KIND, lambda answer: calls.append(1) or 'Done it.')
        token, _ = token_for(user)

        push.record_action(token, 'done')
        again = push.record_action(token, 'done')

        assert calls == [1] and again.confirmation is None

    def test_a_failing_handler_rolls_the_answer_back(self, user, handlers):
        def broken(answer):
            raise RuntimeError('boom')

        push.register_action_handler(KIND, broken)
        token, push_id = token_for(user)

        with pytest.raises(push.ActionFailed):
            push.record_action(token, 'done')

        assert not PushAction.objects.filter(push_id=push_id).exists()

    def test_after_a_failure_a_retry_runs_the_handler_again(self, user, handlers):
        outcomes = [RuntimeError('boom'), 'Done it.']

        def flaky(answer):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        push.register_action_handler(KIND, flaky)
        token, _ = token_for(user)

        with pytest.raises(push.ActionFailed):
            push.record_action(token, 'done')
        assert push.record_action(token, 'done').confirmation == 'Done it.'

    def test_a_long_confirmation_is_cut(self, user, handlers):
        push.register_action_handler(KIND, lambda answer: 'x' * 500)
        token, _ = token_for(user)

        confirmation = push.record_action(token, 'done').confirmation

        assert confirmation is not None and len(confirmation) == push.CONFIRMATION_MAX_LENGTH


class TestActionView:
    def post(self, client, token, action):
        return client.post(
            reverse('core:push-action'),
            data=json.dumps({'token': token, 'action': action}),
            content_type='application/json',
        )

    def test_a_confirmation_is_answered_as_a_message(self, client, user, configured, handlers):
        push.register_action_handler(KIND, lambda answer: 'Snoozed until 14:30.')
        token, _ = token_for(user)

        response = self.post(client, token, 'later')

        assert response.status_code == 200
        assert response.json() == {'message': 'Snoozed until 14:30.'}

    def test_without_a_confirmation_it_is_a_204(self, client, user, configured, handlers):
        token, _ = token_for(user)

        assert self.post(client, token, 'done').status_code == 204

    def test_a_failing_handler_is_a_500_and_records_nothing(
        self, client, user, configured, handlers
    ):
        def broken(answer):
            raise RuntimeError('boom')

        push.register_action_handler(KIND, broken)
        token, push_id = token_for(user)

        response = self.post(client, token, 'done')

        assert response.status_code == 500
        assert response.json() == {'detail': "That didn't work. Open the site to try again."}
        assert not PushAction.objects.filter(push_id=push_id).exists()


class TestKnownPushId:
    def test_send_push_uses_the_id_it_is_given(self, user, configured):
        push.save_subscription(user, subscription_json(FCM + 'device-1'))
        push_id = uuid.uuid4()

        with mock.patch('pywebpush.webpush') as webpush:
            result = push.send_push(user, 'Hello', actions=ACTIONS, kind=KIND, push_id=push_id)

        assert result.push_id == push_id and result.sent == 1
        payload = json.loads(webpush.call_args.kwargs['data'])
        assert payload['data']['push_id'] == str(push_id)

    def test_queue_push_returns_the_id_the_notification_gets(
        self, user, configured, django_capture_on_commit_callbacks
    ):
        push.save_subscription(user, subscription_json(FCM + 'device-1'))

        with mock.patch('pywebpush.webpush') as webpush:
            with django_capture_on_commit_callbacks(execute=True):
                push_id = push.queue_push(user, 'Hello', actions=ACTIONS, kind=KIND)

        payload = json.loads(webpush.call_args.kwargs['data'])
        assert payload['data']['push_id'] == str(push_id)
        # The answer token is for that very push.
        token = payload['data']['action_token']
        assert push.record_action(token, 'done').push_id == push_id

    def test_queue_push_returns_an_id_even_while_push_is_off(self, user, settings):
        settings.VAPID_PUBLIC_KEY = ''

        assert isinstance(push.queue_push(user, 'Hello'), uuid.UUID)


class TestServiceWorker:
    def test_it_shows_the_servers_message_in_place_of_the_notification(self, client):
        body = client.get('/sw.js').content.decode()

        assert 'reply.message' in body
        assert "That didn't work. Open the site to try again." in body
        assert 'showNotification(notification.title' in body
