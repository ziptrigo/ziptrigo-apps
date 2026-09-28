"""Unit tests for `apps.core.ratelimit` (issue #53): the limiter itself (window, reset, keys,
the disabled switch, response builders) rather than any particular protected endpoint -- those
are exercised in each app's own tests (`apps/accounts/tests/...`, `apps/qr_code/tests/...`,
`apps/file_transfer/tests/...`).
"""

import json
import threading

import pytest
from django.http import HttpRequest, JsonResponse
from django.test import RequestFactory

from apps.core import ratelimit
from apps.core.ratelimit.limiter import RateLimitResult

pytestmark = [pytest.mark.unit]

RULES = {'TEST_RULE': (3, 60), 'OTHER_RULE': (3, 60)}


@pytest.fixture()
def enabled(settings):
    """Turns rate limiting on with a small, fixed rule set -- the project-wide default is off
    under pytest (see `config/settings.py`), and most tests here need it on to exercise the
    limiter at all."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = RULES


@pytest.mark.usefixtures('enabled')
class TestHit:
    def test_allows_up_to_the_limit(self):
        results = [ratelimit.hit('same-key', 'TEST_RULE') for _ in range(3)]
        assert [r.allowed for r in results] == [True, True, True]
        assert [r.remaining for r in results] == [2, 1, 0]

    def test_denies_once_over_the_limit(self):
        for _ in range(3):
            ratelimit.hit('same-key', 'TEST_RULE')
        result = ratelimit.hit('same-key', 'TEST_RULE')
        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after > 0

    def test_different_keys_have_independent_counters(self):
        for _ in range(3):
            ratelimit.hit('key-a', 'TEST_RULE')
        # 'key-a' is now at its limit; 'key-b' has never been hit.
        assert ratelimit.hit('key-a', 'TEST_RULE').allowed is False
        assert ratelimit.hit('key-b', 'TEST_RULE').allowed is True

    def test_different_rules_have_independent_counters_for_the_same_key(self):
        for _ in range(3):
            ratelimit.hit('shared-key', 'TEST_RULE')
        assert ratelimit.hit('shared-key', 'TEST_RULE').allowed is False
        # Same key, different rule: its own budget, untouched by the above.
        assert ratelimit.hit('shared-key', 'OTHER_RULE').allowed is True

    def test_window_resets_the_counter(self):
        window_seconds = RULES['TEST_RULE'][1]
        # Aligned to a window boundary, so `base + window_seconds - 1` is still guaranteed to
        # fall in the same window as `base` (see `test_retry_after_...` below for the same need).
        base = (1_700_000_000.0 // window_seconds) * window_seconds
        for _ in range(3):
            ratelimit.hit('windowed-key', 'TEST_RULE', now=base)
        assert ratelimit.hit('windowed-key', 'TEST_RULE', now=base).allowed is False

        # Still the same window a second before it ends: still denied.
        assert (
            ratelimit.hit('windowed-key', 'TEST_RULE', now=base + window_seconds - 1).allowed
            is False
        )
        # One second into the next window: a fresh counter.
        result = ratelimit.hit('windowed-key', 'TEST_RULE', now=base + window_seconds)
        assert result.allowed is True
        assert result.remaining == 2

    def test_retry_after_counts_down_to_the_window_boundary(self):
        window_seconds = RULES['TEST_RULE'][1]
        base = 1_700_000_000.0
        # A window boundary at a whole multiple of window_seconds: hitting exactly on one leaves
        # a full window's worth of retry_after once the limit is reached.
        base = (base // window_seconds) * window_seconds
        for _ in range(3):
            ratelimit.hit('retry-key', 'TEST_RULE', now=base)
        result = ratelimit.hit('retry-key', 'TEST_RULE', now=base)
        assert result.retry_after == window_seconds

        # Halfway through the window, only half the wait remains.
        result = ratelimit.hit('retry-key', 'TEST_RULE', now=base + window_seconds / 2)
        assert result.retry_after == window_seconds / 2


class TestDisabledSwitch:
    """`RATELIMIT_ENABLE` defaults to `False` under pytest (see `config/settings.py`) -- these
    tests rely on that default rather than the `enabled` fixture."""

    def test_always_allows_regardless_of_count(self, settings):
        settings.RATELIMIT_RULES = RULES
        for _ in range(10):
            result = ratelimit.hit('any-key', 'TEST_RULE')
            assert result.allowed is True
            assert result.remaining == -1
            assert result.retry_after == 0

    def test_does_not_touch_the_cache(self, settings):
        """Disabled means genuinely a no-op -- not "allow but still count" -- so flipping the
        switch back on later doesn't inherit a stale count from while it was off."""
        settings.RATELIMIT_RULES = RULES
        for _ in range(10):
            ratelimit.hit('untouched-key', 'TEST_RULE')

        settings.RATELIMIT_ENABLE = True
        # If the 10 hits above had been counted, this key would already be over its limit of 3;
        # a fresh counter proves nothing was recorded while disabled.
        result = ratelimit.hit('untouched-key', 'TEST_RULE')
        assert result.allowed is True
        assert result.remaining == 2


@pytest.mark.usefixtures('enabled')
class TestAtomicity:
    def test_concurrent_hits_never_lose_an_increment(self):
        """`LocMemCache` (this project's cache under pytest) guards `incr`/`add` with its own
        lock, so genuinely concurrent callers must still count every hit exactly once: with a
        limit of 3 and 20 threads racing the same key, exactly 3 must be allowed."""
        results: list[RateLimitResult] = []
        lock = threading.Lock()

        def worker():
            result = ratelimit.hit('race-key', 'TEST_RULE')
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        assert sum(1 for r in results if r.allowed) == 3


@pytest.mark.usefixtures('enabled')
class TestKeyHelpers:
    def _request(self, **meta) -> HttpRequest:
        return RequestFactory().get('/', **meta)

    def test_hit_ip_counts_by_trusted_client_ip(self):
        request = self._request(HTTP_X_REAL_IP='10.0.0.1')
        for _ in range(3):
            ratelimit.hit_ip(request, 'TEST_RULE')
        assert ratelimit.hit_ip(request, 'TEST_RULE').allowed is False

        other = self._request(HTTP_X_REAL_IP='10.0.0.2')
        assert ratelimit.hit_ip(other, 'TEST_RULE').allowed is True

    def test_hit_ip_is_unlimited_when_ip_cannot_be_determined(self):
        request = self._request(HTTP_X_REAL_IP='not-an-ip', REMOTE_ADDR='')
        for _ in range(10):
            assert ratelimit.hit_ip(request, 'TEST_RULE').allowed is True

    def test_hit_value_counts_by_the_given_string(self):
        for _ in range(3):
            ratelimit.hit_value('someone@example.com', 'TEST_RULE')
        assert ratelimit.hit_value('someone@example.com', 'TEST_RULE').allowed is False
        assert ratelimit.hit_value('someone-else@example.com', 'TEST_RULE').allowed is True

    def test_hit_value_is_unlimited_for_empty_or_none(self):
        assert ratelimit.hit_value('', 'TEST_RULE').allowed is True
        assert ratelimit.hit_value(None, 'TEST_RULE').allowed is True

    def test_hit_user_counts_by_pk(self):
        class _FakeUser:
            def __init__(self, pk):
                self.pk = pk

        user = _FakeUser(pk=42)
        for _ in range(3):
            ratelimit.hit_user(user, 'TEST_RULE')
        assert ratelimit.hit_user(user, 'TEST_RULE').allowed is False
        assert ratelimit.hit_user(_FakeUser(pk=43), 'TEST_RULE').allowed is True


class TestEnforce:
    def test_does_nothing_when_allowed(self):
        ratelimit.enforce(RateLimitResult(allowed=True, remaining=1, retry_after=0))

    def test_raises_when_not_allowed(self):
        with pytest.raises(ratelimit.RateLimitExceeded) as excinfo:
            ratelimit.enforce(RateLimitResult(allowed=False, remaining=0, retry_after=42))
        assert excinfo.value.retry_after == 42

    def test_carries_a_custom_message(self):
        with pytest.raises(ratelimit.RateLimitExceeded) as excinfo:
            ratelimit.enforce(
                RateLimitResult(allowed=False, remaining=0, retry_after=1), message='slow down'
            )
        assert excinfo.value.message == 'slow down'


class TestResponses:
    def _result(self, retry_after=30) -> RateLimitResult:
        return RateLimitResult(allowed=False, remaining=0, retry_after=retry_after)

    def test_json_response_shape(self):
        response = ratelimit.json_response(self._result(retry_after=15), message='too fast')
        assert isinstance(response, JsonResponse)
        assert response.status_code == 429
        assert response['Retry-After'] == '15'
        assert json.loads(response.content) == {'error': 'too fast'}

    def test_page_response_is_a_full_page(self, rf):
        request = rf.get('/')
        response = ratelimit.page_response(request, self._result(retry_after=7))
        assert response.status_code == 429
        assert response['Retry-After'] == '7'
        assert b'Too many requests' in response.content

    def test_htmx_response_sets_retarget_headers(self, rf):
        request = rf.get('/', HTTP_HX_REQUEST='true')
        response = ratelimit.htmx_response(request, self._result(), retarget='#msg')
        assert response.status_code == 429
        assert response['HX-Retarget'] == '#msg'
        assert response['HX-Reswap'] == 'innerHTML'

    def test_web_response_picks_htmx_or_page(self, rf):
        htmx_request = rf.get('/', HTTP_HX_REQUEST='true')
        plain_request = rf.get('/')

        htmx_response = ratelimit.web_response(htmx_request, self._result())
        plain_response = ratelimit.web_response(plain_request, self._result())

        assert htmx_response['Content-Type'].startswith('text/html')
        assert b'<html' not in htmx_response.content  # a bare partial, not a full page
        assert b'<html' in plain_response.content.lower() or b'<!DOCTYPE' in plain_response.content
