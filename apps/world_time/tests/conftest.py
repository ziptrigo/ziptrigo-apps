"""Fixtures for the World Time test suite. `user` overrides the root one (no password: no slow
hashing)."""

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.utils import timezone as dj_timezone

from ..models import WorldTimeSettings
from .factories import make_user


@pytest.fixture
def user(db):
    return make_user(email='testuser@example.com', name='Test User', timezone='Europe/Lisbon')


@pytest.fixture
def other_user(db):
    return make_user(name='Other User')


@pytest.fixture
def config(db) -> WorldTimeSettings:
    """The settings row with small caps: 3 tabs, 4 locations per tab."""
    row = WorldTimeSettings.load()
    row.max_tabs_per_user = 3
    row.max_locations_per_tab = 4
    row.save()
    return row


@pytest.fixture
def web(user):
    """A test client logged in as `user`."""
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def freeze_now(monkeypatch):
    """`freeze_now(2026, 10, 28, 12, 0)` makes `timezone.now()` that UTC instant."""

    def freeze(year, month, day, hour=12, minute=0):
        moment = datetime(year, month, day, hour, minute, tzinfo=UTC)
        monkeypatch.setattr(dj_timezone, 'now', lambda: moment)
        return moment

    return freeze
