"""Fixtures shared by every app's tests."""

import logging
from datetime import UTC, datetime

import pytest
from django.core.cache import caches

from apps.accounts.tests.factories import UserFactory
from apps.core.ratelimit import limiter


@pytest.fixture(autouse=True)
def _clear_ratelimit_cache():
    """`apps.core.ratelimit` counters live on their own cache alias (`settings.CACHES['ratelimit']`,
    `LocMemCache` under pytest). Rate limiting itself defaults to disabled under pytest
    (`RATELIMIT_ENABLE`), so this mostly matters for the tests that turn it on with
    `override_settings`: without clearing between tests, a counter from an earlier test could
    still be within the same fixed window (see `apps/core/ratelimit/limiter.py`) and make an
    unrelated, later test see a hit that isn't its own."""
    caches['ratelimit'].clear()
    yield
    caches['ratelimit'].clear()


class RateLimitClock:
    """The wall clock `apps.core.ratelimit.limiter` reads, frozen until a test moves it."""

    #: A multiple of 3600, so a fixed window of 60 s or one hour starts exactly here.
    EPOCH = 500_000 * 3600

    def __init__(self, now: float):
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def ratelimit_clock(monkeypatch):
    """Freeze the clock the rate limiter reads, 1,000 s into an hour (and so 40 s into a minute).

    The limiter's windows are fixed and aligned to the wall clock (see
    `apps/core/ratelimit/limiter.py`), so a test that makes two requests against a limit of 1 per
    window fails whenever the real clock crosses a window boundary between them (the second
    request lands in a new window and is allowed), and one that checks `Retry-After` or the
    "try again in N minutes" message depends on how far into the window the run happens to be.
    Both only show up when the test runs near a boundary or slowly. With the clock frozen a
    window never ends mid-test and `retry_after` is exact: 2,600 s (44 minutes) for an hourly
    rule, 20 s for a one-minute one. `advance()` moves it deliberately.

    Only the limiter's own clock is replaced (its module-level `time`), not `time.time`
    globally, so nothing else (sessions, token expiry, the database) sees the frozen time. Not
    autouse: `core`'s limiter tests pass `now=` themselves, and the storage-error and cache
    paths would not want it.
    """
    clock = RateLimitClock(RateLimitClock.EPOCH + 1000)
    monkeypatch.setattr(limiter, 'time', clock)
    return clock


@pytest.fixture(autouse=True)
def _apps_logs_reach_caplog(monkeypatch):
    """`settings.LOGGING` sets `propagate=False` on the `apps` and `django` loggers (console only,
    no duplicates in production); `caplog` listens on the root logger, so let records through
    while testing. Cosmetic side effect: root keeps its console handler, so pytest's captured
    stderr shows these records twice (once from `django`/`apps`' handler, once from root's);
    `caplog` is unaffected."""
    for name in ('apps', 'django'):
        monkeypatch.setattr(logging.getLogger(name), 'propagate', True)


@pytest.fixture()
def api_client():
    """A Ninja test client for the site's single API (`config.api.api`)."""
    from ninja.testing import TestClient

    from config.api import api

    return TestClient(api)


@pytest.fixture()
def admin_user():
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture()
def regular_user():
    return UserFactory()


@pytest.fixture
def user(db):
    """A user with a confirmed email address."""
    from apps.accounts.models import User

    user = User.objects.create_user(
        email='testuser@example.com',
        password='testpass123',
        name='Test User',
    )
    user.email_confirmed = True
    user.email_confirmed_at = datetime.now(UTC)
    user.save()
    return user
