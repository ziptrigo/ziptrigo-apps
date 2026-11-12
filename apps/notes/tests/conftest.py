"""Fixtures for the Notes test suite. `user` overrides the root one (no password: no slow hashing).

`ImmediateBackend` (the test `TASKS` backend) runs a task inline at `.enqueue()`.
"""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.utils import timezone as dj_timezone

from apps.billing.services import add_credits

from ..models import NotesSettings
from .factories import make_user


@pytest.fixture
def user(db):
    return make_user(email='testuser@example.com', name='Test User')


@pytest.fixture
def other_user(db):
    return make_user(name='Other User')


@pytest.fixture
def config(db) -> NotesSettings:
    """The settings row with 5 free notes, a creation price of 3 and a renewal of 5 per 365 days."""
    row = NotesSettings.load()
    row.free_notes = 5
    row.creation_price = 3
    row.renewal_price = 5
    row.renewal_days = 365
    row.save()
    return row


@pytest.fixture
def funded_user(user, config):
    """`user`, topped up with 100 credits."""
    add_credits(user, 100, description='Test top-up', source='test')
    return user


@pytest.fixture
def web(funded_user):
    """A test client logged in as `funded_user`."""
    client = Client()
    client.force_login(funded_user)
    return client


@pytest.fixture
def freeze_now(monkeypatch):
    """`freeze_now(2026, 10, 28, 12, 0)` makes `timezone.now()` that UTC instant."""

    def freeze(year, month, day, hour=12, minute=0):
        moment = datetime(year, month, day, hour, minute, tzinfo=UTC)
        monkeypatch.setattr(dj_timezone, 'now', lambda: moment)
        return moment

    return freeze


def balance(user) -> int:
    from apps.billing.services import get_balance

    return get_balance(user)
