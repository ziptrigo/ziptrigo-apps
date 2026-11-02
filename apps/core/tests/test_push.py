"""Web Push (issue #239): the subscription model and service, `send_push` (pywebpush mocked), the
answer token, the endpoints, the service worker and manifest, sign-out cleanup, the account page,
the admin, the commands and the system check."""

import json
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock

import pytest
from cryptography.hazmat.primitives import serialization
from django.contrib.auth.models import Permission
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from py_vapid import Vapid
from py_vapid.utils import b64urlencode
from pywebpush import WebPushException

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core import push_checks
from apps.core.models import PushAction, PushSubscription
from apps.core.services import push
from apps.core.signals import push_action

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

FCM = 'https://fcm.googleapis.com/fcm/send/'


def _key_pair() -> tuple[str, str]:
    vapid = Vapid()
    vapid.generate_keys()
    private = vapid.private_key.private_numbers().private_value.to_bytes(32, 'big')
    public = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64urlencode(public), b64urlencode(private)


PUBLIC_KEY, PRIVATE_KEY = _key_pair()


def subscription_json(endpoint: str = FCM + 'device-1', **keys: str) -> dict:
    return {
        'endpoint': endpoint,
        'expirationTime': None,
        'keys': {
            'p256dh': keys.get('p256dh', 'BNcRdreALRFX' + 'a' * 75),
            'auth': keys.get('auth', 'tBHItJI5svbpez7KI4CCXg'),
        },
    }


@pytest.fixture
def configured(settings):
    settings.VAPID_PUBLIC_KEY = PUBLIC_KEY
    settings.VAPID_PRIVATE_KEY = PRIVATE_KEY
    settings.VAPID_SUBJECT = 'mailto:push@example.com'
    return settings


@pytest.fixture
def not_configured(settings):
    settings.VAPID_PUBLIC_KEY = ''
    settings.VAPID_PRIVATE_KEY = ''
    settings.VAPID_SUBJECT = ''
    return settings


@pytest.fixture
def webpush():
    with mock.patch('pywebpush.webpush') as fake:
        yield fake


@pytest.fixture
def web(client, user):
    client.force_login(user)
    return client


def subscribe(user, endpoint: str = FCM + 'device-1') -> PushSubscription:
    return push.save_subscription(user, subscription_json(endpoint))


def failure(status: int) -> WebPushException:
    return WebPushException('Push failed', response=SimpleNamespace(status_code=status, text=''))


def sent_payload(webpush, call: int = 0) -> dict:
    return json.loads(webpush.call_args_list[call].kwargs['data'])


# -- Configuration ------------------------------------------------------------------------------


def test_is_configured_needs_all_three(configured):
    assert push.is_configured()
    assert push.public_key() == PUBLIC_KEY
    configured.VAPID_SUBJECT = ''
    assert not push.is_configured()
    assert push.public_key() == ''


@pytest.mark.parametrize(
    'endpoint',
    [
        'https://fcm.googleapis.com/fcm/send/abc',
        'https://updates.push.services.mozilla.com/wpush/v2/abc',
        'https://web.push.apple.com/QGx',
        'https://wns2-par02p.notify.windows.com/w/?token=abc',
        'https://fcm.googleapis.com:443/fcm/send/abc',
    ],
)
def test_push_service_urls_accepted(endpoint):
    assert push.is_push_service_url(endpoint)


@pytest.mark.parametrize(
    'endpoint',
    [
        'http://fcm.googleapis.com/fcm/send/abc',
        'https://example.com/push',
        'https://fcm.googleapis.com.evil.com/x',
        'https://evilfcm.googleapis.com.example/x',
        'https://user@fcm.googleapis.com/x',
        'https://fcm.googleapis.com:8443/x',
        'https://localhost/x',
        'https://127.0.0.1/x',
        'not a url',
        '',
    ],
)
def test_other_urls_refused(endpoint):
    assert not push.is_push_service_url(endpoint)


# -- Model and subscriptions ---------------------------------------------------------------------


def test_save_creates_a_subscription(user):
    subscription = push.save_subscription(user, subscription_json(), user_agent='Chrome on Android')

    assert subscription.user == user
    assert subscription.endpoint == FCM + 'device-1'
    assert subscription.auth == 'tBHItJI5svbpez7KI4CCXg'
    assert subscription.user_agent == 'Chrome on Android'
    assert subscription.last_success_at is None


def test_save_again_updates_the_keys(user):
    subscribe(user)
    push.save_subscription(user, subscription_json(auth='newauth'))

    assert PushSubscription.objects.count() == 1
    assert PushSubscription.objects.get().auth == 'newauth'


def test_same_browser_moves_to_the_new_user(user):
    other = UserFactory()
    subscribe(other)

    subscribe(user)

    assert list(PushSubscription.objects.values_list('user', flat=True)) == [user.pk]


def test_endpoint_is_unique(user):
    subscribe(user)
    with pytest.raises(Exception):
        PushSubscription.objects.create(user=user, endpoint=FCM + 'device-1', p256dh='a', auth='b')


def test_oldest_device_makes_room(user, monkeypatch):
    monkeypatch.setattr(push, 'MAX_SUBSCRIPTIONS_PER_USER', 3)
    first = subscribe(user, FCM + 'a')
    subscribe(user, FCM + 'b')
    subscribe(user, FCM + 'c')

    subscribe(user, FCM + 'd')

    assert PushSubscription.objects.filter(user=user).count() == 3
    assert not PushSubscription.objects.filter(pk=first.pk).exists()


@pytest.mark.parametrize(
    'data',
    [
        None,
        'nope',
        {'endpoint': FCM + 'x'},
        {'endpoint': FCM + 'x', 'keys': {'p256dh': 'a b', 'auth': 'x'}},
        {'endpoint': FCM + 'x', 'keys': {'p256dh': 'abc', 'auth': ''}},
        {'endpoint': 'https://example.com/x', 'keys': {'p256dh': 'abc', 'auth': 'def'}},
        {'endpoint': FCM + 'x' * 2000, 'keys': {'p256dh': 'abc', 'auth': 'def'}},
    ],
)
def test_invalid_subscriptions_refused(user, data):
    with pytest.raises(ValidationError):
        push.save_subscription(user, data)
    assert not PushSubscription.objects.exists()


def test_delete_only_removes_own(user):
    other = UserFactory()
    subscribe(other, FCM + 'theirs')
    subscribe(user, FCM + 'mine')

    assert not push.delete_subscription(user, FCM + 'theirs')
    assert push.delete_subscription(user, FCM + 'mine')
    assert list(PushSubscription.objects.values_list('endpoint', flat=True)) == [FCM + 'theirs']


def test_subscriptions_go_with_the_user(user):
    subscribe(user)
    user.delete()
    assert not PushSubscription.objects.exists()


# -- send_push -----------------------------------------------------------------------------------


def test_send_does_nothing_when_not_configured(not_configured, user, webpush):
    subscribe(user)

    result = push.send_push(user, 'Hello')

    webpush.assert_not_called()
    assert (result.sent, result.removed, result.failed) == (0, 0, 0)


def test_send_to_every_device(configured, user, webpush):
    subscribe(user, FCM + 'a')
    subscribe(user, FCM + 'b')
    PushSubscription.objects.create(
        user=UserFactory(), endpoint=FCM + 'other', p256dh='x', auth='y'
    )

    result = push.send_push(user, 'Hello', 'Body text', url='/todo/', tag='t1', data={'n': 1})

    assert result.sent == 2
    endpoints = {c.kwargs['subscription_info']['endpoint'] for c in webpush.call_args_list}
    assert endpoints == {FCM + 'a', FCM + 'b'}
    call = webpush.call_args_list[0].kwargs
    assert call['ttl'] == push.DEFAULT_TTL
    assert call['vapid_claims'] == {'sub': 'mailto:push@example.com'}
    assert call['timeout'] == configured.PUSH_TIMEOUT_SECONDS
    payload = sent_payload(webpush)
    assert payload['title'] == 'Hello'
    assert payload['body'] == 'Body text'
    assert payload['tag'] == 't1'
    assert payload['icon'].endswith('app_icon_192.png')
    assert payload['badge'].endswith('push_badge_96.png')
    assert payload['data']['url'] == '/todo/'
    assert payload['data']['n'] == 1
    assert payload['data']['push_id'] == str(result.push_id)
    assert 'actions' not in payload
    assert 'action_token' not in payload['data']
    assert PushSubscription.objects.filter(user=user, last_success_at__isnull=False).count() == 2


def test_send_by_user_id(configured, user, webpush):
    subscribe(user)
    assert push.send_push(user.pk, 'Hello').sent == 1


def test_gone_subscriptions_are_deleted(configured, user, webpush):
    subscribe(user, FCM + 'gone')
    subscribe(user, FCM + 'missing')
    subscribe(user, FCM + 'ok')

    def deliver(**kwargs):
        endpoint = kwargs['subscription_info']['endpoint']
        if endpoint.endswith('gone'):
            raise failure(410)
        if endpoint.endswith('missing'):
            raise failure(404)

    webpush.side_effect = deliver

    result = push.send_push(user, 'Hello')

    assert (result.sent, result.removed, result.failed) == (1, 2, 0)
    assert list(PushSubscription.objects.values_list('endpoint', flat=True)) == [FCM + 'ok']


def test_other_failures_are_logged_and_kept(configured, user, webpush, caplog):
    subscribe(user, FCM + 'busy')
    subscribe(user, FCM + 'down')
    subscribe(user, FCM + 'ok')

    def deliver(**kwargs):
        endpoint = kwargs['subscription_info']['endpoint']
        if endpoint.endswith('busy'):
            raise failure(429)
        if endpoint.endswith('down'):
            raise ConnectionError('no route')

    webpush.side_effect = deliver

    result = push.send_push(user, 'Hello')

    assert (result.sent, result.removed, result.failed) == (1, 0, 2)
    assert PushSubscription.objects.count() == 3
    assert 'status 429' in caplog.text
    # The endpoint is a capability URL: never logged.
    assert FCM not in caplog.text


def test_inactive_user_gets_nothing(configured, user, webpush):
    subscribe(user)
    user.is_active = False
    user.save()

    assert push.send_push(user, 'Hello').sent == 0
    webpush.assert_not_called()


def test_actions_carry_a_token(configured, user, webpush):
    subscribe(user)

    result = push.send_push(
        user,
        'Question?',
        actions=[{'action': 'yes', 'title': 'Yes'}, {'action': 'no', 'title': 'No'}],
        kind='test.kind',
    )

    payload = sent_payload(webpush)
    assert payload['actions'] == [
        {'action': 'yes', 'title': 'Yes'},
        {'action': 'no', 'title': 'No'},
    ]
    assert payload['data']['action_url'] == reverse('core:push-action')
    claims = signing.loads(payload['data']['action_token'], salt=push.ACTION_TOKEN_SALT)
    assert claims == {
        'u': str(user.pk),
        'p': str(result.push_id),
        'k': 'test.kind',
        'a': ['yes', 'no'],
    }


def test_long_text_is_cut(configured, user, webpush):
    subscribe(user)

    push.send_push(user, 'T' * 300, 'B' * 900)

    payload = sent_payload(webpush)
    assert len(payload['title']) == push.TITLE_MAX_LENGTH
    assert len(payload['body']) == push.BODY_MAX_LENGTH


@pytest.mark.parametrize(
    'kwargs',
    [
        {'title': ''},
        {'url': 'https://evil.example/'},
        {'url': '//evil.example/'},
        {'actions': [{'action': 'a', 'title': 'A'}] * 3},
        {'actions': [{'action': 'Bad Id', 'title': 'A'}]},
        {'actions': [{'action': 'a', 'title': ''}]},
        {'actions': [{'action': 'a', 'title': 'A'}, {'action': 'a', 'title': 'B'}]},
        {'data': {'blob': 'x' * 5000}},
    ],
)
def test_bad_arguments_raise(configured, user, webpush, kwargs):
    subscribe(user)
    kwargs.setdefault('title', 'Hello')
    with pytest.raises(ValueError):
        push.send_push(user, **kwargs)
    webpush.assert_not_called()


def test_queue_push_sends_after_commit(
    configured, user, webpush, django_capture_on_commit_callbacks
):
    subscribe(user)

    with django_capture_on_commit_callbacks(execute=True):
        push.queue_push(user, 'Queued', url='/todo/')

    assert sent_payload(webpush)['title'] == 'Queued'


def test_queue_push_checks_arguments_now(configured, user):
    with pytest.raises(ValueError):
        push.queue_push(user, 'Hello', url='https://evil.example/')


def test_queue_push_queues_nothing_when_not_configured(
    not_configured, user, webpush, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        push.queue_push(user, 'Hello')
    assert callbacks == []


# -- Answers ------------------------------------------------------------------------------------


def token_for(user, actions=('yes', 'no'), kind='test.kind'):
    push_id = push.uuid.uuid4()
    return push_id, push.make_action_token(user.pk, push_id, kind, list(actions))


def test_record_action_stores_and_signals_once(user, django_capture_on_commit_callbacks):
    push_id, token = token_for(user)
    received = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    push_action.connect(receiver)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            answer = push.record_action(token, 'yes')
        with django_capture_on_commit_callbacks(execute=True):
            again = push.record_action(token, 'no')
    finally:
        push_action.disconnect(receiver)

    assert again.pk == answer.pk
    assert (answer.user, answer.push_id, answer.kind, answer.action) == (
        user,
        push_id,
        'test.kind',
        'yes',
    )
    assert PushAction.objects.count() == 1
    assert len(received) == 1
    assert received[0]['action'] == 'yes'
    assert received[0]['kind'] == 'test.kind'
    assert received[0]['user_id'] == user.pk


def test_failing_receiver_does_not_break_the_answer(
    user, django_capture_on_commit_callbacks, caplog
):
    _, token = token_for(user)

    def broken(sender, **kwargs):
        raise RuntimeError('boom')

    push_action.connect(broken)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            push.record_action(token, 'yes')
    finally:
        push_action.disconnect(broken)

    assert PushAction.objects.count() == 1
    assert 'push_action receiver' in caplog.text


@pytest.mark.parametrize('action', ['maybe', '', None])
def test_action_not_offered_is_refused(user, action):
    _, token = token_for(user)
    with pytest.raises(push.InvalidActionToken):
        push.record_action(token, action)


def test_tampered_token_is_refused(user):
    _, token = token_for(user)
    with pytest.raises(push.InvalidActionToken):
        push.record_action(token[:-2] + 'xx', 'yes')
    with pytest.raises(push.InvalidActionToken):
        push.record_action(signing.dumps({'u': str(user.pk)}, salt='other'), 'yes')


def test_expired_token_is_refused(user, monkeypatch):
    _, token = token_for(user)
    later = signing.time.time() + push.ACTION_TOKEN_MAX_AGE.total_seconds() + 60
    monkeypatch.setattr(signing.time, 'time', lambda: later)
    with pytest.raises(push.InvalidActionToken):
        push.record_action(token, 'yes')


def test_inactive_user_cannot_answer(user):
    _, token = token_for(user)
    user.is_active = False
    user.save()
    with pytest.raises(push.InvalidActionToken):
        push.record_action(token, 'yes')


# -- Service worker and manifest ------------------------------------------------------------------


def test_service_worker_served_from_the_root(client):
    response = client.get('/sw.js')

    assert response.status_code == 200
    assert response['Content-Type'].startswith('text/javascript')
    assert response['Cache-Control'] == 'no-cache'
    assert response['Service-Worker-Allowed'] == '/'
    body = response.content.decode()
    assert 'showNotification' in body
    assert 'notificationclick' in body


def test_manifest(client):
    response = client.get('/manifest.webmanifest')

    assert response.status_code == 200
    assert response['Content-Type'] == 'application/manifest+json'
    data = json.loads(response.content)
    assert data['name'] == 'ZipTrigo'
    assert data['start_url'] == '/'
    assert data['display'] == 'standalone'
    assert {icon['sizes'] for icon in data['icons']} == {'192x192', '512x512'}
    assert any(icon.get('purpose') == 'maskable' for icon in data['icons'])


def test_base_template_links_the_manifest(client):
    html = client.get(reverse('core:home')).content.decode()
    assert f'<link rel="manifest" href="{reverse("core:manifest")}">' in html


# -- Endpoints ------------------------------------------------------------------------------------


def test_public_key_endpoint(configured, web):
    response = web.get(reverse('core:push-key'))
    assert response.json() == {'enabled': True, 'public_key': PUBLIC_KEY}


def test_public_key_endpoint_when_off(not_configured, web):
    assert web.get(reverse('core:push-key')).json() == {'enabled': False, 'public_key': ''}


@pytest.mark.parametrize('name', ['push-key', 'push-subscribe', 'push-unsubscribe'])
def test_endpoints_need_a_session(configured, client, name):
    method = client.get if name == 'push-key' else client.post
    response = method(reverse(f'core:{name}'))
    assert response.status_code == 302
    assert reverse('accounts:login') in response['Location']


def post_json(client, name, data, **extra):
    return client.post(
        reverse(f'core:{name}'), data=json.dumps(data), content_type='application/json', **extra
    )


def test_subscribe(configured, web, user):
    response = post_json(web, 'push-subscribe', subscription_json(), HTTP_USER_AGENT='Test UA')

    assert response.status_code == 201
    subscription = PushSubscription.objects.get()
    assert response.json() == {'id': str(subscription.pk)}
    assert subscription.user == user
    assert subscription.user_agent == 'Test UA'
    assert web.session[push.SESSION_KEY] == str(subscription.pk)


def test_subscribe_needs_the_csrf_token(configured, user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    assert post_json(client, 'push-subscribe', subscription_json()).status_code == 403

    client.get(reverse('accounts:account'))
    token = client.cookies['csrftoken'].value
    response = post_json(client, 'push-subscribe', subscription_json(), HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 201


@pytest.mark.parametrize('data', [None, 'x', {'endpoint': 'https://example.com/x', 'keys': {}}])
def test_subscribe_refuses_bad_bodies(configured, web, data):
    response = post_json(web, 'push-subscribe', data)
    assert response.status_code == 400
    assert response.json()['detail']
    assert not PushSubscription.objects.exists()


def test_subscribe_refuses_unknown_push_service(configured, web):
    response = post_json(web, 'push-subscribe', subscription_json('https://push.example.com/x'))
    assert response.status_code == 400
    assert response.json() == {'detail': "This browser's push service is not supported."}


def test_subscribe_when_off(not_configured, web):
    assert post_json(web, 'push-subscribe', subscription_json()).status_code == 404


def test_subscribe_is_rate_limited(configured, web, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'PUSH_SUBSCRIBE_USER': (1, 3600)}

    assert post_json(web, 'push-subscribe', subscription_json()).status_code == 201
    response = post_json(web, 'push-subscribe', subscription_json())
    assert response.status_code == 429
    assert 'Retry-After' in response


def test_unsubscribe(configured, web, user):
    post_json(web, 'push-subscribe', subscription_json(FCM + 'mine'))
    subscribe(UserFactory(), FCM + 'theirs')

    assert post_json(web, 'push-unsubscribe', {'endpoint': FCM + 'theirs'}).status_code == 204
    assert post_json(web, 'push-unsubscribe', {'endpoint': FCM + 'mine'}).status_code == 204

    assert list(PushSubscription.objects.values_list('endpoint', flat=True)) == [FCM + 'theirs']
    assert push.SESSION_KEY not in web.session


def test_unsubscribe_needs_an_endpoint(configured, web):
    assert post_json(web, 'push-unsubscribe', {}).status_code == 400


def test_action_endpoint_needs_no_session_or_csrf(user):
    push_id, token = token_for(user)
    client = Client(enforce_csrf_checks=True)

    response = post_json(client, 'push-action', {'token': token, 'action': 'no'})

    assert response.status_code == 204
    assert PushAction.objects.get().action == 'no'
    assert PushAction.objects.get().push_id == push_id
    # A repeat is accepted and changes nothing.
    assert post_json(client, 'push-action', {'token': token, 'action': 'yes'}).status_code == 204
    assert PushAction.objects.get().action == 'no'


@pytest.mark.parametrize('body', [None, 'x', {'token': 'nope', 'action': 'yes'}, {'action': 'yes'}])
def test_action_endpoint_refuses_bad_answers(client, body):
    response = post_json(client, 'push-action', body)
    assert response.status_code == 400
    assert response.json() == {'detail': 'Invalid answer.'}
    assert not PushAction.objects.exists()


def test_action_endpoint_is_post_only(client):
    assert client.get(reverse('core:push-action')).status_code == 405


# -- Sign-out -------------------------------------------------------------------------------------


def test_signing_out_deletes_this_devices_subscription(configured, web, user):
    post_json(web, 'push-subscribe', subscription_json(FCM + 'this-device'))
    subscribe(user, FCM + 'other-device')

    web.post(reverse('accounts:logout'))

    assert list(PushSubscription.objects.values_list('endpoint', flat=True)) == [
        FCM + 'other-device'
    ]


def test_signing_out_leaves_a_subscription_moved_to_another_user(configured, web, user):
    post_json(web, 'push-subscribe', subscription_json(FCM + 'shared'))
    other = UserFactory()
    subscribe(other, FCM + 'shared')

    web.post(reverse('accounts:logout'))

    assert PushSubscription.objects.get().user == other


# -- Pages ----------------------------------------------------------------------------------------


def test_account_page_has_the_section(configured, web):
    html = web.get(reverse('accounts:account')).content.decode()

    assert 'data-push-settings' in html
    assert 'Turn on notifications' in html
    assert 'core/js/push.js' in html
    assert f'data-public-key="{PUBLIC_KEY}"' in html
    assert 'data-synced="0"' in html


def test_script_knows_the_session_saved_a_subscription(configured, web):
    post_json(web, 'push-subscribe', subscription_json())
    html = web.get(reverse('core:home')).content.decode()
    assert 'data-synced="1"' in html


def test_nothing_rendered_when_off(not_configured, web):
    html = web.get(reverse('accounts:account')).content.decode()
    assert 'data-push-settings' not in html
    assert 'core/js/push.js' not in html


def test_no_script_for_anonymous_visitors(configured, client):
    assert 'core/js/push.js' not in client.get(reverse('core:home')).content.decode()


# -- Admin ----------------------------------------------------------------------------------------


CHANGELIST = 'custom_admin:core_pushsubscription_changelist'


def test_admin_lists_the_push_service_not_the_endpoint(configured, client, admin_user, user):
    subscribe(user, FCM + 'secret-token')
    client.force_login(admin_user)

    html = client.get(reverse(CHANGELIST)).content.decode()

    assert 'fcm.googleapis.com' in html
    assert 'secret-token' not in html


def test_admin_sends_the_test_notification(configured, client, admin_user, user, webpush):
    subscribe(user)
    client.force_login(admin_user)

    response = client.post(
        reverse(CHANGELIST),
        {
            'action': 'send_test_notification',
            '_selected_action': [PushSubscription.objects.get().pk],
        },
        follow=True,
    )

    assert response.status_code == 200
    payload = sent_payload(webpush)
    assert payload['title'] == 'Test notification'
    assert [a['action'] for a in payload['actions']] == ['yes', 'no']
    assert payload['data']['url'] == reverse('accounts:account')
    assert '1 device(s) accepted it' in response.content.decode()


def _staff_with(user, *codenames):
    """Make `user` staff holding exactly these permissions."""
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return User.objects.get(pk=user.pk)


def test_staff_without_superuser_cannot_send(configured, client, user):
    staff = _staff_with(UserFactory(), 'view_pushsubscription')
    subscribe(user)
    client.force_login(staff)

    response = client.get(reverse(CHANGELIST))

    assert response.status_code == 200
    assert 'send_test_notification' not in response.content.decode()


def test_answers_admin_lists_answers(client, admin_user, user):
    _, token = token_for(user)
    push.record_action(token, 'yes')
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:core_pushaction_changelist'))

    assert response.status_code == 200
    assert 'test.kind' in response.content.decode()


def test_user_with_push_rows_can_be_deleted_in_the_admin(client, admin_user, user):
    subscribe(user)
    _, token = token_for(user)
    push.record_action(token, 'yes')
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:accounts_user_delete', args=[user.pk]), {'post': 'yes'}
    )

    assert response.status_code == 302
    assert not PushSubscription.objects.exists()
    assert not PushAction.objects.exists()


# -- Commands -------------------------------------------------------------------------------------


def test_generate_vapid_keys_prints_a_working_pair():
    out = StringIO()
    call_command('generate_vapid_keys', stdout=out)

    lines = dict(line.split('=', 1) for line in out.getvalue().splitlines())
    vapid = Vapid.from_string(private_key=lines['VAPID_PRIVATE_KEY'])
    public = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert b64urlencode(public) == lines['VAPID_PUBLIC_KEY']


def test_send_test_push_command(configured, user, webpush):
    subscribe(user)
    out = StringIO()

    call_command('send_test_push', user.email.upper(), stdout=out)

    assert 'Sent to 1 device(s): 1 accepted' in out.getvalue()
    assert sent_payload(webpush)['title'] == 'Test notification'


def test_send_test_push_command_errors(configured, not_configured, user):
    with pytest.raises(CommandError, match='not configured'):
        call_command('send_test_push', user.email)


def test_send_test_push_command_unknown_user_or_no_device(configured, user):
    with pytest.raises(CommandError, match='No user'):
        call_command('send_test_push', 'nobody@example.com')
    with pytest.raises(CommandError, match='no device'):
        call_command('send_test_push', user.email)


def test_send_test_push_command_when_no_device_accepts(configured, user, webpush):
    subscribe(user)
    webpush.side_effect = failure(500)
    with pytest.raises(CommandError, match='No device accepted'):
        call_command('send_test_push', user.email, stdout=StringIO())


# -- System check ---------------------------------------------------------------------------------


def ids(settings) -> list[str]:
    return [warning.id for warning in push_checks.check_vapid_settings()]


def test_check_quiet_with_none_or_all(configured, settings):
    assert ids(settings) == []
    settings.VAPID_PUBLIC_KEY = settings.VAPID_PRIVATE_KEY = settings.VAPID_SUBJECT = ''
    assert ids(settings) == []


def test_check_warns_about_a_partial_setup(configured, settings):
    settings.VAPID_PRIVATE_KEY = ''
    assert ids(settings) == ['core.W020']


def test_check_warns_about_a_bad_subject(configured, settings):
    settings.VAPID_SUBJECT = 'push@example.com'
    assert ids(settings) == ['core.W021']


def test_token_age_is_longer_than_the_ttl():
    assert push.ACTION_TOKEN_MAX_AGE > timedelta(seconds=push.DEFAULT_TTL)
