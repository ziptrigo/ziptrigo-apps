"""Web Push (issue #239): the service worker and manifest at the site root, the subscription
endpoints the account page's script calls, and the endpoint the service worker posts an answer to.

The subscription endpoints are JSON in and out, session-authenticated and CSRF-protected (the
script sends the token in `X-CSRFToken`). The answer endpoint is the exception: a service worker
can't read the CSRF cookie and may run when the device's session has expired, so it is
CSRF-exempt and authenticated by the signed token the notification carries
(`apps.core.services.push.record_action`).
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .. import ratelimit
from ..http import AuthenticatedHttpRequest
from ..services import push

#: The service worker's source. Served from the site root rather than `/static/`, because a
#: service worker only controls pages under its own path.
SERVICE_WORKER_PATH = Path(__file__).resolve().parents[1] / 'static' / 'core' / 'js' / 'sw.js'
#: The browser re-fetches both on its own schedule; Cloudflare and nginx must not keep a stale copy.
NO_CACHE = 'no-cache'
#: A subscription is a few hundred bytes; anything much larger isn't one.
MAX_BODY_BYTES = 4096


@lru_cache(maxsize=1)
def _service_worker_source() -> str:
    return SERVICE_WORKER_PATH.read_text(encoding='utf-8')


def _json_body(request: HttpRequest) -> Any:
    if len(request.body) > MAX_BODY_BYTES:
        return None
    try:
        return json.loads(request.body)
    except ValueError, UnicodeDecodeError:
        return None


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({'detail': message}, status=status)


@require_GET
def service_worker(request: HttpRequest) -> HttpResponse:
    """`/sw.js`: the service worker, scoped to the whole site."""
    response = HttpResponse(_service_worker_source(), content_type='text/javascript')
    response['Cache-Control'] = NO_CACHE
    response['Service-Worker-Allowed'] = '/'
    return response


@require_GET
def manifest(request: HttpRequest) -> HttpResponse:
    """`/manifest.webmanifest`: makes the site installable (an iPhone only delivers push to a site
    added to the home screen) and names it on Android's notification settings."""
    data = {
        'id': '/',
        'name': 'ZipTrigo',
        'short_name': 'ZipTrigo',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#f4f7f6',
        'theme_color': '#8fa89e',
        'icons': [
            {
                'src': static('core/images/app_icon_192.png'),
                'sizes': '192x192',
                'type': 'image/png',
            },
            {
                'src': static('core/images/app_icon_512.png'),
                'sizes': '512x512',
                'type': 'image/png',
            },
            {
                'src': static('core/images/app_icon_maskable_512.png'),
                'sizes': '512x512',
                'type': 'image/png',
                'purpose': 'maskable',
            },
        ],
    }
    response = JsonResponse(data, content_type='application/manifest+json')
    response['Cache-Control'] = NO_CACHE
    return response


@login_required
@require_GET
@never_cache
def push_public_key(request: AuthenticatedHttpRequest) -> JsonResponse:
    """`{"enabled": ..., "public_key": ...}`: the VAPID public key to subscribe with, `""` and
    `enabled: false` while push is not configured."""
    key = push.public_key()
    return JsonResponse({'enabled': bool(key), 'public_key': key})


@login_required
@require_POST
def push_subscribe(request: AuthenticatedHttpRequest) -> JsonResponse:
    """Save this browser's subscription (`PushSubscription.toJSON()` as the body) for the user.

    Answers 201 `{"id": ...}`, 400 `{"detail": ...}` for a body that isn't a subscription to a
    known push service, 404 while push is not configured and a JSON 429 over
    `PUSH_SUBSCRIBE_USER`, which every call counts against. The subscription's id is kept in the
    session, so signing out deletes it.
    """
    if not push.is_configured():
        return _error('Push notifications are not available.', status=404)
    limited = ratelimit.hit_user(request.user, 'PUSH_SUBSCRIBE_USER')
    if not limited.allowed:
        return ratelimit.json_response(limited)
    data = _json_body(request)
    try:
        subscription = push.save_subscription(
            request.user, data, user_agent=request.headers.get('User-Agent', '')
        )
    except ValidationError as exc:
        return _error(' '.join(exc.messages))
    push.remember_in_session(request.session, subscription)
    return JsonResponse({'id': str(subscription.pk)}, status=201)


@login_required
@require_POST
def push_unsubscribe(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Delete the user's subscription whose `endpoint` the body names (`{"endpoint": ...}`). 204
    whether or not there was one (the browser has unsubscribed either way)."""
    data = _json_body(request)
    endpoint = data.get('endpoint') if isinstance(data, dict) else None
    if not isinstance(endpoint, str) or not endpoint:
        return _error('Invalid subscription.')
    push.delete_subscription(request.user, endpoint)
    request.session.pop(push.SESSION_KEY, None)
    return HttpResponse(status=204)


@csrf_exempt
@require_POST
def push_action(request: HttpRequest) -> HttpResponse:
    """Record the button a user tapped on a notification: `{"token": ..., "action": ...}`, posted
    by the service worker. 204 when recorded (or already recorded), 400 for anything else, the same
    answer for every kind of bad token."""
    data = _json_body(request)
    if not isinstance(data, dict):
        return _error('Invalid answer.')
    try:
        push.record_action(data.get('token'), data.get('action'))
    except push.InvalidActionToken:
        return _error('Invalid answer.')
    return HttpResponse(status=204)
