"""`apps.accounts.services.captcha` (issue #117): the server half of Cloudflare Turnstile, with
Cloudflare's siteverify endpoint mocked -- the suite never touches the network."""

import pytest
import requests
from django.core import checks

from apps.accounts import checks as accounts_checks
from apps.accounts.services import captcha

pytestmark = pytest.mark.unit

REAL_SECRET = '0x4AAAAAAAreal-secret'
TEST_SECRET = '1x0000000000000000000000000000000AA'


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code}')

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture
def siteverify(monkeypatch, settings):
    """Answer siteverify with `.reply` (a `FakeResponse`, or an exception to raise); record calls."""
    settings.TURNSTILE_SITE_KEY = 'site-key'
    settings.TURNSTILE_SECRET_KEY = REAL_SECRET
    settings.BASE_URL = 'https://app.ziptrigo.com'

    class Stub:
        reply: FakeResponse | Exception = FakeResponse(
            {'success': True, 'hostname': 'app.ziptrigo.com', 'action': captcha.ACTION}
        )
        calls: list[dict] = []

    stub = Stub()
    stub.calls = []

    def fake_post(url, data, timeout):
        stub.calls.append({'url': url, 'data': data, 'timeout': timeout})
        if isinstance(stub.reply, Exception):
            raise stub.reply
        return stub.reply

    monkeypatch.setattr(captcha.requests, 'post', fake_post)
    return stub


def test_is_configured_needs_both_keys(settings):
    settings.TURNSTILE_SITE_KEY, settings.TURNSTILE_SECRET_KEY = 'site', ''
    assert not captcha.is_configured()
    settings.TURNSTILE_SITE_KEY, settings.TURNSTILE_SECRET_KEY = '', 'secret'
    assert not captcha.is_configured()
    settings.TURNSTILE_SITE_KEY, settings.TURNSTILE_SECRET_KEY = 'site', 'secret'
    assert captcha.is_configured()


def test_valid_token_passes_and_sends_secret_token_and_ip(siteverify):
    assert captcha.verify('tok', '203.0.113.7') is True

    (call,) = siteverify.calls
    assert call['url'] == captcha.SITEVERIFY_URL
    assert call['data'] == {'secret': REAL_SECRET, 'response': 'tok', 'remoteip': '203.0.113.7'}
    assert call['timeout'] == captcha.TIMEOUT_SECONDS


def test_ip_is_optional(siteverify):
    assert captcha.verify('tok') is True
    assert 'remoteip' not in siteverify.calls[0]['data']


@pytest.mark.parametrize('token', ['', 'x' * (captcha.MAX_TOKEN_LENGTH + 1)])
def test_empty_or_oversized_token_fails_without_a_call(siteverify, token):
    assert captcha.verify(token) is False
    assert siteverify.calls == []


@pytest.mark.parametrize(
    'reply',
    [
        FakeResponse({'success': False, 'error-codes': ['invalid-input-response']}),
        FakeResponse({'success': 'true', 'hostname': 'app.ziptrigo.com', 'action': 'register'}),
        FakeResponse({'success': True, 'hostname': 'evil.example', 'action': 'register'}),
        FakeResponse({'success': True, 'hostname': 'app.ziptrigo.com', 'action': 'contact'}),
        FakeResponse({'success': True, 'hostname': 'app.ziptrigo.com'}),
        FakeResponse(['not', 'a', 'dict']),
        FakeResponse(ValueError('not json')),
        FakeResponse({}, status_code=500),
        requests.Timeout('slow'),
        requests.ConnectionError('down'),
    ],
    ids=[
        'rejected',
        'success-not-a-bool',
        'wrong-hostname',
        'wrong-action',
        'missing-action',
        'not-a-dict',
        'not-json',
        'http-500',
        'timeout',
        'connection-error',
    ],
)
def test_fails_closed(siteverify, reply):
    siteverify.reply = reply

    assert captcha.verify('tok') is False


def test_test_secret_skips_hostname_and_action_checks(siteverify, settings):
    """Cloudflare's test secrets answer with placeholder hostname/action values."""
    settings.TURNSTILE_SECRET_KEY = TEST_SECRET
    siteverify.reply = FakeResponse({'success': True, 'hostname': 'example.com', 'action': ''})

    assert captcha.verify('XXXX.DUMMY.TOKEN.XXXX') is True


def test_token_is_never_logged(siteverify, caplog):
    siteverify.reply = FakeResponse({'success': False, 'error-codes': ['timeout-or-duplicate']})

    captcha.verify('secret-token-value')

    assert 'timeout-or-duplicate' in caplog.text
    assert 'secret-token-value' not in caplog.text


@pytest.mark.parametrize('values', [('site', ''), ('', 'secret')])
def test_check_warns_about_a_partial_configuration(settings, values):
    settings.TURNSTILE_SITE_KEY, settings.TURNSTILE_SECRET_KEY = values

    (warning,) = accounts_checks.check_turnstile()

    assert isinstance(warning, checks.Warning)
    assert warning.id == 'accounts.W002'


@pytest.mark.parametrize('values', [('', ''), ('site', 'secret')])
def test_check_is_quiet_when_fully_configured_or_not_at_all(settings, values):
    settings.TURNSTILE_SITE_KEY, settings.TURNSTILE_SECRET_KEY = values

    assert accounts_checks.check_turnstile() == []
