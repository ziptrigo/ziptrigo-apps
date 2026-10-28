"""Fixtures for the todo test suite. `user` overrides the root one (no password: no slow hashing).

`ImmediateBackend` (the test `TASKS` backend) runs a task inline at `.enqueue()` and swallows its
exceptions, so assert on database state, and use `django_capture_on_commit_callbacks(execute=True)`
around anything queued on commit.
"""

from datetime import UTC, date, datetime

import pytest
from django.test import Client
from django.utils import timezone as dj_timezone

from apps.billing.services import add_credits

from ..models import TodoSettings
from .factories import make_user


@pytest.fixture
def user(db):
    return make_user(email='testuser@example.com', name='Test User')


@pytest.fixture
def other_user(db):
    return make_user()


@pytest.fixture
def config(db) -> TodoSettings:
    """The settings row with 2 free tasks a month and a price of 5."""
    row = TodoSettings.load()
    row.free_tasks_per_month = 2
    row.task_price = 5
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


@pytest.fixture
def wednesday(freeze_now) -> date:
    """Today is Wednesday 2026-10-28 (UTC noon)."""
    freeze_now(2026, 10, 28)
    return date(2026, 10, 28)


def balance(user) -> int:
    from apps.billing.services import get_balance

    return get_balance(user)
