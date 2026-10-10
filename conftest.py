"""Fixtures shared by every app's tests."""

import logging
from datetime import UTC, datetime

import pytest
from django.core.cache import caches

from apps.accounts.tests.factories import UserFactory


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


@pytest.fixture(autouse=True)
def _apps_logs_reach_caplog(monkeypatch):
    """`settings.LOGGING` sets `propagate=False` on the `apps` logger (console only, no duplicates
    in production); `caplog` listens on the root logger, so let records through while testing."""
    monkeypatch.setattr(logging.getLogger('apps'), 'propagate', True)


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
