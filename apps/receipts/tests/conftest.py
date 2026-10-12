"""Fixtures for the receipts test suite. `user` comes from the root `conftest.py`.

Two are autouse so no test can reach the network by accident: the S3 storage and the AI client are
replaced with in-memory fakes. Remember that the test `TASKS` backend is `ImmediateBackend`: a task
runs inline at `.enqueue()` and its exceptions are swallowed, so assert on database state, and use
`django_capture_on_commit_callbacks(execute=True)` around anything that queues work `on_commit`.
"""

import pytest

from apps.billing.models import BillingSettings
from apps.billing.services import add_credits

from ..models import Receipt, ReceiptsSettings
from ..services import ai, storage
from ..services.accounts import get_account
from .fakes import FakeAIClient, FakeReceiptsStorage


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch) -> FakeReceiptsStorage:
    fake = FakeReceiptsStorage()
    monkeypatch.setattr(storage, '_storage', fake)
    return fake


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch) -> FakeAIClient:
    fake = FakeAIClient()
    monkeypatch.setattr(ai, '_client', fake)
    return fake


@pytest.fixture
def sent_emails(monkeypatch):
    """Capture what the receipts emails hand to `send_email` instead of sending anything."""
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.receipts.services.emails.send_email', lambda **kwargs: sent.append(kwargs) or (1, 0)
    )
    return sent


@pytest.fixture
def receipts_settings(db) -> ReceiptsSettings:
    return ReceiptsSettings.load()


@pytest.fixture
def billing_settings(db) -> BillingSettings:
    return BillingSettings.load()


@pytest.fixture
def funded_user(user):
    """`user`, topped up with credits."""
    add_credits(user, 100, description='Test top-up', source='test')
    return user


@pytest.fixture
def account(funded_user):
    return get_account(funded_user)


@pytest.fixture
def make_receipt(funded_user, fake_storage):
    """Create a stored, uploaded `Receipt` for `funded_user` (status `received` unless given)
    with `data` in the fake bucket. Does not queue processing."""
    from .factories import create_receipt

    def _make(**kwargs) -> Receipt:
        return create_receipt(funded_user, fake_storage, **kwargs)

    return _make
