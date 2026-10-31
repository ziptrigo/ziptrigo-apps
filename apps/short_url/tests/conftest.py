"""Fixtures for the short_url test suite. `user` comes from the root `conftest.py`.

`no_network` is autouse: title fetching is the one place this app makes a request, and no test may
reach the network by accident. Tests of the fetcher replace the two functions it uses.
`ImmediateBackend` (the test `TASKS` backend) runs a task inline at `.enqueue()` and swallows its
exceptions, so assert on database state, and use `django_capture_on_commit_callbacks(execute=True)`
around anything queued on commit.
"""

import pytest
from django.test import Client

from apps.billing.services import add_credits

from ..models import ShortUrlSettings
from ..services import reputation, safe_browsing, titles
from .factories import make_user


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _refuse(*args, **kwargs):
        raise AssertionError('a test tried to reach the network')

    monkeypatch.setattr(titles, 'resolve_public', _refuse)
    monkeypatch.setattr(titles, 'http_get', _refuse)
    monkeypatch.setattr(safe_browsing.requests, 'post', _refuse)


@pytest.fixture(autouse=True)
def safe_browsing_off(settings):
    """No Safe Browsing key unless a test sets one, and no verdict or client left over from
    another test."""
    settings.SAFE_BROWSING_API_KEY = ''
    reputation.reset_cache()
    safe_browsing.set_client(None)
    yield
    reputation.reset_cache()
    safe_browsing.set_client(None)


#: What a browser sends. A request with no `User-Agent` counts as a bot, which the test client
#: would otherwise be, so every test client here introduces itself as a person.
BROWSER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)


@pytest.fixture
def client():
    return Client(HTTP_USER_AGENT=BROWSER_AGENT)


@pytest.fixture(autouse=True)
def fast_password_hasher(settings):
    """Link passwords are hashed with Django's hasher; a cheap one keeps the tests fast."""
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


@pytest.fixture(autouse=True)
def geoip_off(settings):
    """No GeoIP database unless a test installs a fake reader."""
    from ..services import geoip

    settings.GEOIP_PATH = ''
    geoip.reset()
    yield
    geoip.reset()


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
