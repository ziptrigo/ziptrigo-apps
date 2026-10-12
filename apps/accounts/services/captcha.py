"""Cloudflare Turnstile captcha for the register page (issue #117).

The browser half is Cloudflare's widget (`accounts/partials/turnstile_widget.html` and
`accounts/js/turnstile.js`), which puts a single-use token in the form's `cf-turnstile-response`
field. This module is the server half: `verify` sends that token to Cloudflare's siteverify
endpoint and says whether it is good. Same approach as joao-coelho.com's contact form.

Enabled only when both `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` are set (`is_configured`);
with neither, the widget is hidden and the register view skips the check, which is how pytest and a
local run without keys work.

Fails closed: once enabled, anything other than a clear yes from Cloudflare -- a timeout, a non-200,
an unparseable reply -- rejects the signup. A Cloudflare outage blocks new accounts for its
duration; logins are unaffected. This is the opposite of `apps.core.ratelimit`'s fail-open choice,
on purpose: a captcha that lets everyone through when it can't reach Cloudflare protects nothing
from a bot that can make it time out.

With a real secret, the reply's `hostname` must be `BASE_URL`'s host and its `action` must be
`ACTION`, as Cloudflare recommends, so a token minted for another site or another form sharing the
same widget can't be replayed here. Cloudflare's test secrets (`TEST_SECRET_KEYS`) answer with
placeholder values for both, so the two checks are skipped for them.
"""

import logging
from urllib.parse import urlsplit

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SITEVERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
RESPONSE_FIELD = 'cf-turnstile-response'
ACTION = 'register'
TIMEOUT_SECONDS = 5
MAX_TOKEN_LENGTH = 2048  # Cloudflare's documented maximum.

# https://developers.cloudflare.com/turnstile/troubleshooting/testing/
TEST_SECRET_KEYS = frozenset(
    {
        '1x0000000000000000000000000000000AA',  # always passes
        '2x0000000000000000000000000000000AA',  # always fails
        '3x0000000000000000000000000000000AA',  # "token already spent"
    }
)


def is_configured() -> bool:
    """Whether the captcha is on: both keys set."""
    return bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY)


def verify(token: str, remote_ip: str | None = None) -> bool:
    """Ask Cloudflare whether `token` is a valid, unused Turnstile token for this site.

    Never raises; any failure is logged and returns `False`. The token is never logged.
    """
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return False

    data = {'secret': settings.TURNSTILE_SECRET_KEY, 'response': token}
    if remote_ip:
        data['remoteip'] = remote_ip

    try:
        response = requests.post(SITEVERIFY_URL, data=data, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        result = response.json()
    except requests.RequestException, ValueError:
        logger.exception('Turnstile siteverify call failed')
        return False

    if not isinstance(result, dict) or result.get('success') is not True:
        error_codes = result.get('error-codes') if isinstance(result, dict) else None
        logger.info('Turnstile rejected a token: %s', error_codes)
        return False

    if settings.TURNSTILE_SECRET_KEY in TEST_SECRET_KEYS:
        return True

    expected_host = urlsplit(settings.BASE_URL).hostname
    if result.get('hostname') != expected_host:
        logger.warning(
            'Turnstile token for host %r, expected %r', result.get('hostname'), expected_host
        )
        return False
    if result.get('action') != ACTION:
        logger.warning('Turnstile token for action %r, expected %r', result.get('action'), ACTION)
        return False
    return True
