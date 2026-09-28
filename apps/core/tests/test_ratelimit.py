"""Unit tests for `apps.core.ratelimit` (issue #53): the limiter itself (window, reset, keys,
storage backends, the disabled switch, response builders) rather than any particular protected
endpoint -- those are exercised in each app's own tests (`apps/accounts/tests/...`,
`apps/qr_code/tests/...`, `apps/file_transfer/tests/...`).

`settings.RATELIMIT_STORAGE` defaults to `'db'` under pytest, same as dev/prod without
`CACHE_URL` (see `config/settings.py`) -- so every test below that enables rate limiting exercises
`apps.core.models.RateLimitCounter` "naturally", and the module is marked `django_db` throughout.
`TestCachePathBackend` opts into the `'ratelimit'` LocMem cache alias instead
(`override_settings(RATELIMIT_STORAGE='cache')`) to keep that path covered too.
"""

import json
import threading

import pytest
from django.http import HttpRequest, JsonResponse
from django.test import RequestFactory
from django.utils import timezone

from apps.core import ratelimit
from apps.core.models import RateLimitCounter
from apps.core.ratelimit.limiter import RateLimitResult

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

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

    def test_does_not_touch_storage(self, settings):
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
    def test_concurrent_ish_hits_never_lose_an_increment(self):
        """20 back-to-back calls through the real `ratelimit.hit()` -> `_db_increment` path (one
        Django connection, like a single worker process handling requests in quick succession)
        must count every one exactly once: with a limit of 3, exactly 3 are allowed and the final
        stored count is exactly 20, never fewer (a lost update) or more (a double count)."""
        results = [ratelimit.hit('race-key', 'TEST_RULE') for _ in range(20)]

        assert sum(1 for r in results if r.allowed) == 3
        assert RateLimitCounter.objects.get().count == 20

    def test_the_upsert_sql_is_safe_under_real_concurrent_writers(self, tmp_path):
        """The atomic-upsert *SQL pattern* `_db_increment` relies on (`INSERT ... ON CONFLICT
        ("key") DO UPDATE SET "count" = "count" + 1 RETURNING "count"`) must never lose an
        increment under genuinely concurrent writers -- proven here with real OS threads against
        a real on-disk SQLite file and 20 threads racing the same row.

        This deliberately bypasses Django's own connection (`sqlite3` directly, own temp file)
        rather than `apps.core.models.RateLimitCounter` through `django.db.connection`: pytest's
        default *test* database for SQLite is an in-memory, shared-cache database (Django's own
        default when `DATABASES[...]['TEST']['NAME']` isn't set), and SQLite's shared-cache mode
        has its own, unrelated locking model that rejects concurrent writers from separate
        connections outright (`OperationalError: database table is locked`) -- a well-known
        SQLite quirk of that mode specifically, not of the upsert pattern, and not something
        production ever hits (Postgres has no such mode; an on-disk SQLite file, as this test
        uses, doesn't either -- confirmed separately while building this test). What's under test
        here is the SQL construct itself, which is identical to what `_db_increment` runs.
        """
        import sqlite3

        db_path = str(tmp_path / 'ratelimit_concurrency.sqlite3')
        setup_conn = sqlite3.connect(db_path)
        setup_conn.execute(
            'CREATE TABLE core_ratelimitcounter ('
            '"key" varchar(64) NOT NULL PRIMARY KEY, "count" integer NOT NULL, "expires_at" datetime NOT NULL)'
        )
        setup_conn.commit()
        setup_conn.close()

        results: list[int | str] = []
        lock = threading.Lock()

        def worker():
            conn = sqlite3.connect(db_path, timeout=15)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    'INSERT INTO core_ratelimitcounter ("key", "count", "expires_at") '
                    "VALUES ('race-key', 1, '2099-01-01 00:00:00') "
                    'ON CONFLICT ("key") DO UPDATE SET "count" = "count" + 1 '
                    'RETURNING "count"'
                )
                row = cursor.fetchone()
                conn.commit()
                outcome: int | str = row[0]
            except Exception as exc:  # pragma: no cover - only on a genuine regression
                outcome = repr(exc)
            finally:
                conn.close()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(r for r in results if isinstance(r, int)) == list(range(1, 21))
        assert not [r for r in results if isinstance(r, str)]  # no errors of any kind


@pytest.mark.usefixtures('enabled')
class TestDbStorageBackend:
    """Issue #53 code review: the DB storage path (`apps.core.models.RateLimitCounter`) replaced
    a `DatabaseCache`-backed design with two bugs -- `DatabaseCache.incr` silently shortening any
    window over 5 minutes (its fallback `get`-then-`set` used the cache's *default* timeout, not
    the bucket's real one), and `MAX_ENTRIES` culling live counters under ordinary traffic. These
    tests exercise the DB path directly and would have caught both.
    """

    def test_second_hit_keeps_the_original_window_end_past_300_seconds(self, settings):
        """A window longer than 5 minutes (`DatabaseCache`'s old default timeout) must still
        report a `retry_after` counting down to the *original* window end on a later hit within
        the same window, not one reset to some shorter default."""
        window_seconds = 600
        settings.RATELIMIT_RULES = {**RULES, 'LONG_RULE': (10, window_seconds)}
        base = (1_700_000_000.0 // window_seconds) * window_seconds

        first = ratelimit.hit('long-window-key', 'LONG_RULE', now=base)
        assert first.retry_after == window_seconds

        # 350 seconds later: past the old `DatabaseCache` default timeout (300s), but still
        # inside this 600s window.
        second = ratelimit.hit('long-window-key', 'LONG_RULE', now=base + 350)
        assert second.allowed is True
        assert second.remaining == 8
        # Still counting down to the *same* window end -- 600 - 350 = 250 -- not reset.
        assert second.retry_after == window_seconds - 350

    def test_many_other_keys_do_not_evict_a_counter(self):
        """No `MAX_ENTRIES`-style culling on the DB path: creating hundreds of other rows must
        never touch an unrelated counter (the `DatabaseCache` default culled past 300 rows,
        expired-first then a third of the rest in `cache_key` order)."""
        ratelimit.hit('the-one-that-matters', 'TEST_RULE')
        ratelimit.hit('the-one-that-matters', 'TEST_RULE')

        for i in range(400):
            ratelimit.hit(f'unrelated-{i}', 'TEST_RULE')

        result = ratelimit.hit('the-one-that-matters', 'TEST_RULE')
        assert result.allowed is True
        assert result.remaining == 0  # this is the 3rd hit against a limit of 3

    def test_window_over_300_seconds_holds_across_more_than_300_seconds_of_fake_time(
        self, settings
    ):
        window_seconds = 900
        settings.RATELIMIT_RULES = {**RULES, 'LONG_RULE': (2, window_seconds)}
        base = (1_700_000_000.0 // window_seconds) * window_seconds
        ratelimit.hit('holds-key', 'LONG_RULE', now=base)
        ratelimit.hit('holds-key', 'LONG_RULE', now=base + 310)
        # A third hit, over 600s after the first, must still land in the same window and
        # therefore still be denied (limit of 2), not silently reset.
        result = ratelimit.hit('holds-key', 'LONG_RULE', now=base + 620)
        assert result.allowed is False

    def test_peek_does_not_create_or_increment_a_row(self):
        assert RateLimitCounter.objects.count() == 0
        result = ratelimit.peek('peek-only-key', 'TEST_RULE')
        assert result.allowed is True
        assert RateLimitCounter.objects.count() == 0

    def test_hit_then_peek_reports_the_same_count_without_adding_to_it(self):
        ratelimit.hit('peek-key', 'TEST_RULE')
        ratelimit.hit('peek-key', 'TEST_RULE')

        first_peek = ratelimit.peek('peek-key', 'TEST_RULE')
        second_peek = ratelimit.peek('peek-key', 'TEST_RULE')
        assert first_peek.remaining == second_peek.remaining == 1

        # A real hit afterwards still sees exactly 2 prior hits, proving the peeks above never
        # counted as one.
        result = ratelimit.hit('peek-key', 'TEST_RULE')
        assert result.allowed is True
        assert result.remaining == 0

    def test_purge_expired_deletes_only_rows_whose_window_has_passed(self):
        window_seconds = RULES['TEST_RULE'][1]
        base = (1_700_000_000.0 // window_seconds) * window_seconds

        ratelimit.hit('old-window-key', 'TEST_RULE', now=base)
        ratelimit.hit('current-window-key', 'TEST_RULE', now=base + window_seconds)

        assert RateLimitCounter.objects.count() == 2

        deleted = ratelimit.purge_expired(now=base + window_seconds + 1)

        assert deleted == 1
        remaining = list(RateLimitCounter.objects.values_list('key', flat=True))
        assert len(remaining) == 1

    def test_purge_expired_is_a_noop_on_the_cache_storage_path(self, settings):
        settings.RATELIMIT_STORAGE = 'cache'
        assert ratelimit.purge_expired() == 0

    def test_keys_are_hashed_never_stored_raw(self):
        """Issue #53 code review: a raw email (or any other caller-supplied value) must never
        appear in storage -- `RateLimitCounter.key` is a fixed-length hash."""
        ratelimit.hit_value('someone@example.com', 'TEST_RULE')

        row = RateLimitCounter.objects.get()
        assert 'someone@example.com' not in row.key
        assert len(row.key) == 64  # a hex-encoded SHA-256 digest

    def test_fails_open_and_logs_on_a_storage_error(self, monkeypatch, caplog):
        def _boom(*args, **kwargs):
            raise RuntimeError('storage is down')

        monkeypatch.setattr('apps.core.ratelimit.limiter._db_increment', _boom)

        with caplog.at_level('ERROR', logger='apps.core.ratelimit.limiter'):
            result = ratelimit.hit('doomed-key', 'TEST_RULE')

        assert result.allowed is True
        assert result.remaining == -1
        assert 'failing open' in caplog.text

    def test_logs_the_rule_name_when_a_limit_is_exceeded(self, caplog):
        for _ in range(3):
            ratelimit.hit('logged-key', 'TEST_RULE')

        with caplog.at_level('INFO', logger='apps.core.ratelimit.limiter'):
            ratelimit.hit('logged-key', 'TEST_RULE')

        assert 'TEST_RULE' in caplog.text
        assert 'logged-key' not in caplog.text  # never the raw key


@pytest.mark.usefixtures('enabled')
class TestCachePathBackend:
    """The Redis/LocMem storage path (`RATELIMIT_STORAGE = 'cache'`), used in dev/prod when
    `CACHE_URL` is set. Exercised here against the `'ratelimit'` LocMem cache alias that's still
    configured under pytest for exactly this (issue #53 code review: "keep LocMem/Redis path
    tests too")."""

    @pytest.fixture(autouse=True)
    def _cache_storage(self, settings):
        settings.RATELIMIT_STORAGE = 'cache'

    def test_allows_up_to_the_limit_then_denies(self):
        for _ in range(3):
            assert ratelimit.hit('cache-key', 'TEST_RULE').allowed is True
        assert ratelimit.hit('cache-key', 'TEST_RULE').allowed is False

    def test_second_hit_keeps_the_original_ttl_past_300_seconds(self, settings):
        """`LocMemCache.incr` (like `RedisCache`'s) mutates the stored value in place without
        touching the key's expiry -- unlike the old `DatabaseCache` design, a window longer than
        5 minutes must still hold together on this path too."""
        window_seconds = 600
        settings.RATELIMIT_RULES = {**RULES, 'LONG_RULE': (10, window_seconds)}
        base = (1_700_000_000.0 // window_seconds) * window_seconds
        ratelimit.hit('cache-long-key', 'LONG_RULE', now=base)
        second = ratelimit.hit('cache-long-key', 'LONG_RULE', now=base + 350)
        assert second.retry_after == window_seconds - 350

    def test_concurrent_hits_never_lose_an_increment(self):
        """`LocMemCache` guards `incr`/`add` with its own lock, so genuinely concurrent callers
        must still count every hit exactly once."""
        results: list[RateLimitResult] = []
        lock = threading.Lock()

        def worker():
            result = ratelimit.hit('cache-race-key', 'TEST_RULE')
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        assert sum(1 for r in results if r.allowed) == 3

    def test_peek_does_not_increment(self):
        ratelimit.hit('cache-peek-key', 'TEST_RULE')
        assert ratelimit.peek('cache-peek-key', 'TEST_RULE').remaining == 2
        assert ratelimit.peek('cache-peek-key', 'TEST_RULE').remaining == 2


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

    def test_hit_ip_normalises_ipv6_to_its_64(self):
        """Issue #53 code review: two IPv6 addresses in the same /64 (the block size most
        ISPs hand a single customer) must share one counter, or a host that merely rotates its
        own address within that /64 could dodge the limit for free."""
        first = self._request(HTTP_X_REAL_IP='2001:db8:abcd:1234::1')
        second = self._request(HTTP_X_REAL_IP='2001:db8:abcd:1234:ffff:ffff:ffff:ffff')

        for _ in range(3):
            ratelimit.hit_ip(first, 'TEST_RULE')
        assert ratelimit.hit_ip(second, 'TEST_RULE').allowed is False

    def test_hit_ip_treats_different_64s_independently(self):
        first = self._request(HTTP_X_REAL_IP='2001:db8:abcd:1234::1')
        second = self._request(HTTP_X_REAL_IP='2001:db8:abcd:9999::1')

        for _ in range(3):
            ratelimit.hit_ip(first, 'TEST_RULE')
        assert ratelimit.hit_ip(second, 'TEST_RULE').allowed is True

    def test_hit_ip_unwraps_ipv4_mapped_addresses(self):
        """`::ffff:203.0.113.5` and `203.0.113.5` must count against the same budget -- otherwise
        an IPv4-mapped representation would silently dodge whatever limit the plain IPv4 form is
        already subject to."""
        mapped = self._request(HTTP_X_REAL_IP='::ffff:203.0.113.5')
        plain = self._request(HTTP_X_REAL_IP='203.0.113.5')

        for _ in range(3):
            ratelimit.hit_ip(mapped, 'TEST_RULE')
        assert ratelimit.hit_ip(plain, 'TEST_RULE').allowed is False

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

    def test_peek_value_does_not_increment(self):
        ratelimit.hit_value('peeked@example.com', 'TEST_RULE')
        assert ratelimit.peek_value('peeked@example.com', 'TEST_RULE').allowed is True
        assert ratelimit.peek_value('peeked@example.com', 'TEST_RULE').allowed is True
        # Still only 1 real hit recorded -- 2 more get through before denial.
        assert ratelimit.hit_value('peeked@example.com', 'TEST_RULE').allowed is True
        assert ratelimit.hit_value('peeked@example.com', 'TEST_RULE').allowed is True
        assert ratelimit.hit_value('peeked@example.com', 'TEST_RULE').allowed is False

    def test_peek_value_is_unlimited_for_empty_or_none(self):
        assert ratelimit.peek_value('', 'TEST_RULE').allowed is True
        assert ratelimit.peek_value(None, 'TEST_RULE').allowed is True

    def test_hit_ip_and_value_counts_by_the_combination(self):
        request = self._request(HTTP_X_REAL_IP='10.0.0.1')
        other_ip = self._request(HTTP_X_REAL_IP='10.0.0.2')

        for _ in range(3):
            ratelimit.hit_ip_and_value(request, 'victim@example.com', 'TEST_RULE')
        assert (
            ratelimit.hit_ip_and_value(request, 'victim@example.com', 'TEST_RULE').allowed is False
        )

        # Same email, different IP: independent budget.
        assert (
            ratelimit.hit_ip_and_value(other_ip, 'victim@example.com', 'TEST_RULE').allowed is True
        )
        # Same IP, different email: also independent.
        assert (
            ratelimit.hit_ip_and_value(request, 'someone-else@example.com', 'TEST_RULE').allowed
            is True
        )

    def test_hit_ip_and_value_is_unlimited_when_either_side_is_missing(self):
        request = self._request(HTTP_X_REAL_IP='not-an-ip', REMOTE_ADDR='')
        assert (
            ratelimit.hit_ip_and_value(request, 'someone@example.com', 'TEST_RULE').allowed is True
        )

        with_ip = self._request(HTTP_X_REAL_IP='10.0.0.1')
        assert ratelimit.hit_ip_and_value(with_ip, '', 'TEST_RULE').allowed is True
        assert ratelimit.hit_ip_and_value(with_ip, None, 'TEST_RULE').allowed is True


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


def test_now_argument_is_not_wall_clock_dependent():
    """Sanity check that `timezone.now()` (used by the DB path's `expires_at`) and the `now=`
    epoch float tests pass around are talking about the same present -- if this ever drifted,
    every `now=`-pinned test above would be silently exercising the wrong window."""
    import time

    assert abs(time.time() - timezone.now().timestamp()) < 5
