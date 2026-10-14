"""Integration test for the `credits_added` -> re-enable-suspended-transfers wiring registered in
`apps/file_transfer/apps.py`'s `_connect_signals`."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.services import add_credits, get_balance, spend_credits

from ..models import TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_topping_up_credits_reenables_suspended_transfers(
    draft_transfer, django_capture_on_commit_callbacks
):
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now() - timedelta(hours=1)
    draft_transfer.save()
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')
    assert get_balance(draft_transfer.owner) == 0

    with django_capture_on_commit_callbacks(execute=True):
        add_credits(draft_transfer.owner, 10, description='top up', source='test')

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.suspended_at is None


def test_topping_up_below_the_days_cost_does_not_reenable(
    draft_transfer, billing_settings, django_capture_on_commit_callbacks
):
    """1 credit passes the old flat minimum but not a 3-credit day: the receiver must leave the
    transfer suspended, then re-enable it once the balance covers the day."""
    billing_settings.storage_price_per_gb_per_day = Decimal('3.0')
    billing_settings.save()
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now() - timedelta(hours=1)
    draft_transfer.size_bytes = 1024**3
    draft_transfer.save()
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    with django_capture_on_commit_callbacks(execute=True):
        add_credits(draft_transfer.owner, 1, description='top up', source='test')
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED

    with django_capture_on_commit_callbacks(execute=True):
        add_credits(draft_transfer.owner, 2, description='top up', source='test')
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
