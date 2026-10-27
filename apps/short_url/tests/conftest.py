"""Fixtures for the short_url test suite. `user` comes from the root `conftest.py`.

`no_network` is autouse: title fetching is the one place this app makes a request, and no test may
reach the network by accident. Tests of the fetcher replace the two functions it uses.
`ImmediateBackend` (the test `TASKS` backend) runs a task inline at `.enqueue()` and swallows its
exceptions, so assert on database state, and use `django_capture_on_commit_callbacks(execute=True)`
around anything queued on commit.
"""

import pytest

from apps.billing.services import add_credits

from ..models import ShortUrlSettings
from ..services import titles
from .factories import make_user


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _refuse(*args, **kwargs):
        raise AssertionError('a test tried to reach the network')

    monkeypatch.setattr(titles, 'resolve_public', _refuse)
    monkeypatch.setattr(titles, 'http_get', _refuse)


@pytest.fixture(autouse=True)
def site_url(settings):
    """A known site address, so loop checks and short URLs don't depend on `.env.local`."""
    settings.BASE_URL = 'https://app.example.test'


@pytest.fixture
def user(db):
    """A confirmed user without a password (hashing one costs ~0.1 s a test), overriding the root
    `user` fixture, which these tests never log in with by password."""
    return make_user(email='testuser@example.com', name='Test User')


@pytest.fixture
def prices(db) -> ShortUrlSettings:
    """The settings row, with every price at 1 and the allowance at 2 free active links (so a
    third active link is an "extra" one), which the tests then change as they need."""
    row = ShortUrlSettings.load()
    row.custom_alias_price = 1
    row.password_price = 1
    row.analytics_price = 1
    row.extra_link_price = 1
    row.free_active_links = 2
    row.max_active_links = 5
    row.save()
    return row


@pytest.fixture
def funded_user(user, prices):
    """`user`, topped up with 10 credits."""
    add_credits(user, 10, description='Test top-up', source='test')
    return user


@pytest.fixture
def web(client, funded_user):
    """A test client logged in as `funded_user`."""
    client.force_login(funded_user)
    return client


@pytest.fixture
def other_user(db):
    return make_user()


def balance(user) -> int:
    from apps.billing.services import get_balance

    return get_balance(user)
