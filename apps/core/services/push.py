"""Web Push notifications (issue #239): a browser's subscription, sending to a user's devices, and
the answers a user gives with a notification's action buttons.

How it fits together:

- The browser (`core/js/push.js`, on the account page) registers the service worker served at
  `/sw.js`, asks for permission on a click, subscribes with `VAPID_PUBLIC_KEY` and posts the
  subscription to `POST /push/subscribe/` (`save_subscription`). The subscription's id is kept in
  the session, so signing out deletes it (`forget_session_subscription`, from the
  `user_logged_out` receiver in `CoreConfig`): a shared device stops getting the previous user's
  pushes. After a new sign-in on a browser that still has permission, the script posts the same
  subscription again, without asking.
- `send_push` encrypts a JSON payload for each of the user's subscriptions and POSTs it to the
  push service (`pywebpush`), signed with `VAPID_PRIVATE_KEY`. A subscription the push service
  answers 404 or 410 for is gone for good and deleted; any other failure is logged and the next
  device is tried. `queue_push` does the same from the `default` task queue, after the caller's
  transaction commits, for a caller that is answering a request.
- A notification with action buttons carries a signed token (`make_action_token`) bound to the
  user, the push and its buttons. The service worker posts it with the button that was tapped to
  `POST /push/action/` (`record_action`), which needs no session or CSRF token (a service worker
  has neither the CSRF cookie nor, perhaps, a live session) and stores one `PushAction` per push,
  then sends `apps.core.signals.push_action` for a product to act on.

Push is off while any of `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT` is unset
(`is_configured`): the account page shows no button and `send_push` sends nothing.

Browsers limit what a notification can show: Chrome (desktop and Android) shows at most two action
buttons, and Safari on iPhone none, so `send_push` refuses more than `MAX_ACTIONS` and a caller
must not rely on the buttons alone (a tap on the notification opens its `url`).
"""

import json
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Model
from django.tasks import task
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from ..models import PushAction, PushSubscription
from ..models.push import (
    ACTION_MAX_LENGTH,
    AUTH_MAX_LENGTH,
    ENDPOINT_MAX_LENGTH,
    KIND_MAX_LENGTH,
    P256DH_MAX_LENGTH,
    USER_AGENT_MAX_LENGTH,
)
from ..signals import push_action
from .notifications import is_safe_url
from .queue import enqueue_after_commit

logger = logging.getLogger(__name__)

#: How long a push service keeps a notification for a device that is offline (seconds).
DEFAULT_TTL = 24 * 60 * 60
#: Chrome shows at most two action buttons (`Notification.maxActions`); more would be dropped.
MAX_ACTIONS = 2
#: A user's devices. Past it the oldest subscription makes room: an old one is usually a browser
#: that was reset or thrown away without unsubscribing.
MAX_SUBSCRIPTIONS_PER_USER = 20
#: How long a notification's buttons keep working. Longer than `DEFAULT_TTL`, since a
#: notification can sit in the tray for days before it is answered.
ACTION_TOKEN_MAX_AGE = timedelta(days=7)
ACTION_TOKEN_SALT = 'core.push.action'
#: The session key holding the id of this browser's subscription, for the sign-out cleanup.
SESSION_KEY = 'push_subscription_id'
#: Same caps as in-app notifications (`Notification`): the text is often something a user typed.
TITLE_MAX_LENGTH = 140
BODY_MAX_LENGTH = 500
TAG_MAX_LENGTH = 100
#: Push services take about 4 KB of encrypted payload; this leaves room for the encryption.
MAX_PAYLOAD_BYTES = 3_500
#: Notification images, relative to the site (the service worker resolves them).
ICON_PATH = 'core/images/app_icon_192.png'
BADGE_PATH = 'core/images/push_badge_96.png'

#: Push services a subscription may point at, by host suffix: the server POSTs to the endpoint, so
#: an arbitrary URL would let a user make it call any address (an internal one included). Google
#: (Chrome, Edge on Android, Opera, Samsung Internet, Brave), Mozilla (Firefox), Apple (Safari)
#: and Microsoft (Edge on Windows). A browser with another push service can't subscribe until its
#: host is added here.
PUSH_SERVICE_HOSTS = (
    'fcm.googleapis.com',
    'android.googleapis.com',
    'push.services.mozilla.com',
    'push.apple.com',
    'notify.windows.com',
)

_ACTION_ID = re.compile(r'^[a-z0-9_-]{1,50}$')
_BASE64URL = re.compile(r'^[A-Za-z0-9_-]+={0,2}$')


class InvalidActionToken(Exception):
    """The answer's token is malformed, forged, expired, not for that button, or its user is gone
    or inactive. The view answers 400 whichever it is."""


@dataclass(frozen=True)
class PushResult:
    """What `send_push` did: `push_id` is the notification's id (shared by every device),
    `sent` the devices the push service accepted it for, `removed` the subscriptions it said are
    gone (deleted), `failed` the others."""

    push_id: UUID
    sent: int = 0
    removed: int = 0
    failed: int = 0


def is_configured() -> bool:
    """Whether push can work: all three VAPID settings are set."""
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY and settings.VAPID_SUBJECT)


def public_key() -> str:
    """The VAPID public key browsers subscribe with (base64url), `''` while push is off."""
    return settings.VAPID_PUBLIC_KEY if is_configured() else ''


def is_push_service_url(endpoint: str) -> bool:
    """Whether `endpoint` is an https URL on one of `PUSH_SERVICE_HOSTS` (or a subdomain), with no
    credentials or port of its own."""
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not host or port not in (None, 443) or '@' in parts.netloc:
        return False
    return any(host == allowed or host.endswith('.' + allowed) for allowed in PUSH_SERVICE_HOSTS)


def _clean_key(value: Any, max_length: int, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValidationError({name: 'Invalid key.'})
    if not _BASE64URL.match(value):
        raise ValidationError({name: 'Invalid key.'})
    return value


def clean_subscription(data: Any) -> tuple[str, str, str]:
    """The `(endpoint, p256dh, auth)` of a subscription as the browser's `PushSubscription.toJSON()`
    gives it (`{"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}`), or a `ValidationError`."""
    if not isinstance(data, Mapping):
        raise ValidationError('Invalid subscription.')
    endpoint = data.get('endpoint')
    if not isinstance(endpoint, str) or len(endpoint) > ENDPOINT_MAX_LENGTH:
        raise ValidationError({'endpoint': 'Invalid endpoint.'})
    if not is_push_service_url(endpoint):
        raise ValidationError({'endpoint': "This browser's push service is not supported."})
    keys = data.get('keys')
    if not isinstance(keys, Mapping):
        raise ValidationError({'keys': 'Invalid key.'})
    p256dh = _clean_key(keys.get('p256dh'), P256DH_MAX_LENGTH, 'p256dh')
    auth = _clean_key(keys.get('auth'), AUTH_MAX_LENGTH, 'auth')
    return endpoint, p256dh, auth


def save_subscription(user: Model, data: Any, *, user_agent: str = '') -> PushSubscription:
    """Store the browser subscription `data` for `user` and return it.

    The endpoint identifies the browser, so a subscription another user had is moved to `user`
    (the same browser, signed in to another account) and one `user` had is updated (new keys after
    the browser renewed it). A user at `MAX_SUBSCRIPTIONS_PER_USER` devices loses the oldest.
    Raises `ValidationError` for anything that isn't a subscription to a known push service.
    """
    endpoint, p256dh, auth = clean_subscription(data)
    values = {
        'user': user,
        'p256dh': p256dh,
        'auth': auth,
        'user_agent': (user_agent or '')[:USER_AGENT_MAX_LENGTH],
    }
    with transaction.atomic():
        # The user's row serialises their concurrent subscribes, so the cap holds.
        get_user_model().objects.select_for_update().filter(pk=user.pk).first()
        try:
            with transaction.atomic():
                subscription, _ = PushSubscription.objects.update_or_create(
                    endpoint=endpoint, defaults=values
                )
        except IntegrityError:
            # Another request created the same endpoint first: take it over.
            subscription = PushSubscription.objects.select_for_update().get(endpoint=endpoint)
            for field, value in values.items():
                setattr(subscription, field, value)
            subscription.save()
        others = PushSubscription.objects.filter(user=user).exclude(pk=subscription.pk)
        excess = others.count() - (MAX_SUBSCRIPTIONS_PER_USER - 1)
        if excess > 0:
            oldest = list(others.order_by('created_at').values_list('pk', flat=True)[:excess])
            PushSubscription.objects.filter(pk__in=oldest).delete()
    return subscription


def delete_subscription(user: Model, endpoint: str) -> bool:
    """Delete `user`'s subscription with `endpoint` (turning push off on that browser). Another
    user's is left alone. Returns whether one was deleted."""
    if not isinstance(endpoint, str) or not endpoint:
        return False
    deleted, _ = PushSubscription.objects.filter(user=user, endpoint=endpoint).delete()
    return bool(deleted)


def remember_in_session(session, subscription: PushSubscription) -> None:
    """Remember which subscription this browser has, for `forget_session_subscription`."""
    session[SESSION_KEY] = str(subscription.pk)


def forget_session_subscription(session, user: Model | None) -> None:
    """Delete the subscription this session saved, if it is still `user`'s: called on sign-out so
    the device stops getting that user's pushes."""
    subscription_id = session.get(SESSION_KEY) if session is not None else None
    if not subscription_id or user is None or user.pk is None:
        return
    try:
        PushSubscription.objects.filter(pk=subscription_id, user=user).delete()
    except ValueError, ValidationError:
        pass
    session.pop(SESSION_KEY, None)


def make_action_token(user_id: Any, push_id: UUID, kind: str, actions: Sequence[str]) -> str:
    """A signed, timestamped token that lets whoever holds it answer push `push_id` of `user_id`
    with one of `actions`. It travels inside the encrypted payload, so only the subscribed browser
    sees it."""
    return signing.dumps(
        {'u': str(user_id), 'p': str(push_id), 'k': kind, 'a': list(actions)},
        salt=ACTION_TOKEN_SALT,
        compress=True,
    )


def record_action(token: str, action: str) -> PushAction:
    """Record the answer `action` to the push its `token` is for and return the `PushAction`.

    The first answer to a push is kept: a repeat (the service worker retrying, or the same token
    sent twice) returns the stored row and signals nothing. The `push_action` signal is sent once,
    after commit. Raises `InvalidActionToken` for a bad, expired or foreign token, an action the
    notification didn't offer, and a user who is gone or inactive.
    """
    if not isinstance(token, str) or not isinstance(action, str):
        raise InvalidActionToken
    try:
        claims = signing.loads(
            token, salt=ACTION_TOKEN_SALT, max_age=ACTION_TOKEN_MAX_AGE.total_seconds()
        )
        user_id = claims['u']
        push_id = UUID(claims['p'])
        kind = str(claims['k'])
        allowed = claims['a']
    except signing.BadSignature, KeyError, TypeError, ValueError:
        raise InvalidActionToken from None
    if action not in allowed:
        raise InvalidActionToken
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None or not user.is_active:
        raise InvalidActionToken
    with transaction.atomic():
        answer, created = PushAction.objects.get_or_create(
            push_id=push_id,
            defaults={'user': user, 'kind': kind[:KIND_MAX_LENGTH], 'action': action},
        )
        if created:
            transaction.on_commit(lambda: _send_push_action(answer))
    return answer


def _send_push_action(answer: PushAction) -> None:
    results = push_action.send_robust(
        sender=PushAction,
        user_id=answer.user_id,
        push_id=answer.push_id,
        kind=answer.kind,
        action=answer.action,
        push_action=answer,
    )
    for receiver, result in results:
        if isinstance(result, Exception):
            logger.error(
                'push_action receiver %r failed', receiver, exc_info=(type(result), result, None)
            )


def _truncate(text: str, limit: int) -> str:
    text = ' '.join((text or '').split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + '…'


def _clean_actions(actions: Sequence[Mapping[str, str]] | None) -> list[dict[str, str]]:
    if not actions:
        return []
    if len(actions) > MAX_ACTIONS:
        raise ValueError(f'A push notification shows at most {MAX_ACTIONS} actions.')
    cleaned = []
    for item in actions:
        action_id, title = item.get('action'), item.get('title')
        if not isinstance(action_id, str) or not _ACTION_ID.match(action_id):
            raise ValueError(f'Invalid action id: {action_id!r}')
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f'Action {action_id!r} needs a title.')
        cleaned.append({'action': action_id[:ACTION_MAX_LENGTH], 'title': _truncate(title, 40)})
    if len({a['action'] for a in cleaned}) != len(cleaned):
        raise ValueError('Action ids must differ.')
    return cleaned


def build_payload(
    user_id: Any,
    push_id: UUID,
    *,
    title: str,
    body: str = '',
    url: str | None = None,
    actions: Sequence[Mapping[str, str]] | None = None,
    tag: str | None = None,
    data: Mapping[str, Any] | None = None,
    kind: str = '',
) -> dict[str, Any]:
    """The JSON the service worker turns into a notification (`showNotification`'s options)."""
    title = _truncate(title, TITLE_MAX_LENGTH)
    if not title:
        raise ValueError('A push notification needs a title.')
    if url is not None and not is_safe_url(url):
        raise ValueError('url must be a same-site path, e.g. reverse(...).')
    cleaned_actions = _clean_actions(actions)
    payload_data: dict[str, Any] = dict(data or {})
    payload_data.update({'push_id': str(push_id), 'url': url or '/'})
    if cleaned_actions:
        payload_data['action_url'] = reverse('core:push-action')
        payload_data['action_token'] = make_action_token(
            user_id, push_id, kind, [a['action'] for a in cleaned_actions]
        )
    payload: dict[str, Any] = {
        'title': title,
        'body': _truncate(body, BODY_MAX_LENGTH),
        'icon': static(ICON_PATH),
        'badge': static(BADGE_PATH),
        'data': payload_data,
    }
    if tag:
        payload['tag'] = tag[:TAG_MAX_LENGTH]
    if cleaned_actions:
        payload['actions'] = cleaned_actions
    return payload


@lru_cache(maxsize=4)
def _vapid(private_key: str):
    from py_vapid import Vapid

    return Vapid.from_string(private_key=private_key)


def _deliver(subscription: PushSubscription, payload: str, ttl: int) -> None:
    from pywebpush import webpush

    webpush(
        subscription_info={
            'endpoint': subscription.endpoint,
            'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth},
        },
        data=payload,
        vapid_private_key=_vapid(settings.VAPID_PRIVATE_KEY),
        # A fresh dict per call: pywebpush writes `aud` and `exp` into it.
        vapid_claims={'sub': settings.VAPID_SUBJECT},
        ttl=ttl,
        timeout=settings.PUSH_TIMEOUT_SECONDS,
        headers={'Urgency': 'normal'},
    )


def send_push(
    user: Model | UUID | str,
    title: str,
    body: str = '',
    url: str | None = None,
    actions: Sequence[Mapping[str, str]] | None = None,
    tag: str | None = None,
    data: Mapping[str, Any] | None = None,
    *,
    kind: str = '',
    ttl: int = DEFAULT_TTL,
) -> PushResult:
    """Send a notification to every device `user` (an instance, or its id) subscribed.

    `title` and `body` are plain text, cut to `TITLE_MAX_LENGTH` / `BODY_MAX_LENGTH`. `url` is a
    same-site path (built with `reverse`) the notification opens when tapped, the home page when
    `None`. `actions` are up to `MAX_ACTIONS` buttons, `[{'action': 'yes', 'title': 'Yes'}, ...]`
    (ids of `a-z 0-9 _ -`); an answer is stored as a `PushAction` with this `kind` and sent as the
    `push_action` signal. `tag` replaces an earlier notification with the same tag on the device.
    `data` is extra JSON for the service worker. Bad arguments are programming errors and raise
    `ValueError`.

    Sends nothing while push is not configured or the user is inactive. A device the push service
    says is gone (404, 410) has its subscription deleted; any other failure is logged and skipped,
    so this never raises for a delivery problem. Each device is a network call, so a caller that
    is answering a request uses `queue_push` instead.
    """
    push_id = uuid.uuid4()
    user_id = user.pk if isinstance(user, Model) else user
    payload = build_payload(
        user_id,
        push_id,
        title=title,
        body=body,
        url=url,
        actions=actions,
        tag=tag,
        data=data,
        kind=kind,
    )
    encoded = json.dumps(payload, separators=(',', ':'))
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError('The push payload is too large; send less in data.')
    if not is_configured():
        logger.info('Push is not configured (VAPID_* settings): nothing sent')
        return PushResult(push_id=push_id)
    if not get_user_model().objects.filter(pk=user_id, is_active=True).exists():
        return PushResult(push_id=push_id)

    from pywebpush import WebPushException

    sent = removed = failed = 0
    for subscription in PushSubscription.objects.filter(user_id=user_id).order_by('created_at'):
        try:
            _deliver(subscription, encoded, ttl)
        except WebPushException as exc:
            status = getattr(exc.response, 'status_code', None)
            if status in (404, 410):
                # Unsubscribed, expired or the browser was reset: it will never work again.
                subscription.delete()
                removed += 1
            else:
                # The endpoint is a capability URL and the response may echo it: the id only.
                logger.warning(
                    'Push to subscription %s failed (status %s)', subscription.pk, status
                )
                failed += 1
        except Exception:
            logger.exception('Push to subscription %s failed', subscription.pk)
            failed += 1
        else:
            PushSubscription.objects.filter(pk=subscription.pk).update(
                last_success_at=timezone.now()
            )
            sent += 1
    return PushResult(push_id=push_id, sent=sent, removed=removed, failed=failed)


#: The test notification's buttons (`send_test_push`): Chrome shows two at most.
TEST_ACTIONS = ({'action': 'yes', 'title': 'Yes'}, {'action': 'no', 'title': 'No'})
TEST_KIND = 'core.test'


def send_test_push(user: Model) -> PushResult:
    """Send the test notification (`manage.py send_test_push`, the subscription admin's action):
    a Yes / No question whose answer is stored as a `PushAction` of kind `core.test`."""
    return send_push(
        user,
        'Test notification',
        'Did this reach you? Answer with a button, or tap to open your account page.',
        url=reverse('accounts:account'),
        actions=TEST_ACTIONS,
        tag='core-test',
        kind=TEST_KIND,
    )


@task
def send_push_task(
    user_id: str,
    title: str,
    body: str = '',
    url: str | None = None,
    actions: list[dict[str, str]] | None = None,
    tag: str | None = None,
    data: dict[str, Any] | None = None,
    kind: str = '',
    ttl: int = DEFAULT_TTL,
) -> None:
    """`send_push` on the `default` queue (`queue_push` queues it)."""
    send_push(user_id, title, body, url, actions, tag, data, kind=kind, ttl=ttl)


def queue_push(
    user: Model | UUID | str,
    title: str,
    body: str = '',
    url: str | None = None,
    actions: Sequence[Mapping[str, str]] | None = None,
    tag: str | None = None,
    data: Mapping[str, Any] | None = None,
    *,
    kind: str = '',
    ttl: int = DEFAULT_TTL,
) -> None:
    """Queue `send_push` once the surrounding transaction commits. The arguments are checked now
    (a `ValueError` here, not in the worker); nothing is queued while push is not configured."""
    user_id = str(user.pk if isinstance(user, Model) else user)
    build_payload(
        user_id,
        uuid.uuid4(),
        title=title,
        body=body,
        url=url,
        actions=actions,
        tag=tag,
        data=data,
        kind=kind,
    )
    if not is_configured():
        return
    enqueue_after_commit(
        send_push_task,
        user_id,
        title,
        body,
        url,
        [dict(a) for a in actions] if actions else None,
        tag,
        dict(data) if data else None,
        kind,
        ttl,
    )
