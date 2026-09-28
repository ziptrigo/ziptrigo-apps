from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import CreditTransaction
from apps.billing.services import get_balance, spend_credits

from ..models import TransferStatus
from ..services import metering

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

_ONE_GB = 1024**3


def _make_active(transfer, *, size_bytes, last_billed_at=None):
    transfer.status = TransferStatus.ACTIVE
    transfer.size_bytes = size_bytes
    transfer.last_billed_at = last_billed_at or (timezone.now() - timedelta(hours=25))
    transfer.save()
    return transfer


def test_meter_transfer_skips_when_not_due(draft_transfer, ft_settings):
    _make_active(draft_transfer, size_bytes=_ONE_GB, last_billed_at=timezone.now())
    balance_before = get_balance(draft_transfer.owner)

    metering.meter_transfer(draft_transfer, ft_settings)

    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 0
    assert get_balance(draft_transfer.owner) == balance_before


def test_meter_transfer_rounds_down_and_charges_every_third_day(draft_transfer, ft_settings):
    ft_settings.price_per_gb_per_day = Decimal('0.4')
    ft_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    balance_before = get_balance(draft_transfer.owner)

    # Day 1: accrues 0.4, nothing charged yet.
    metering.meter_transfer(draft_transfer, ft_settings)
    assert draft_transfer.billed_days == 1
    assert draft_transfer.accrued == Decimal('0.4')
    assert get_balance(draft_transfer.owner) == balance_before

    # Day 2: accrues to 0.8, still nothing charged.
    draft_transfer.last_billed_at = timezone.now() - timedelta(hours=25)
    draft_transfer.save()
    metering.meter_transfer(draft_transfer, ft_settings)
    assert draft_transfer.billed_days == 2
    assert draft_transfer.accrued == Decimal('0.8')
    assert get_balance(draft_transfer.owner) == balance_before

    # Day 3: accrues to 1.2 -- floor(1.2) = 1 credit spent, 0.2 remains.
    draft_transfer.last_billed_at = timezone.now() - timedelta(hours=25)
    draft_transfer.save()
    metering.meter_transfer(draft_transfer, ft_settings)
    assert draft_transfer.billed_days == 3
    assert draft_transfer.accrued == Decimal('0.2')
    assert draft_transfer.credits_charged == 1
    assert get_balance(draft_transfer.owner) == balance_before - 1

    tx = CreditTransaction.objects.filter(user=draft_transfer.owner, source='file_transfer').get()
    assert tx.amount == -1
    assert 'day 3' in tx.description
    assert draft_transfer.display_name in tx.description or 'Untitled transfer' in tx.description


def test_meter_transfer_advances_last_billed_at_by_exactly_one_period(draft_transfer, ft_settings):
    """Advancing to `last_billed_at + 24h` rather than to `now` matters at the margins: a tick
    that lands a little early each day would otherwise never quite reach the 24h mark and skip a
    day's charge (see the docstring on `meter_transfer`)."""
    start = timezone.now() - timedelta(hours=25)
    _make_active(draft_transfer, size_bytes=_ONE_GB, last_billed_at=start)

    metering.meter_transfer(draft_transfer, ft_settings)

    draft_transfer.refresh_from_db()
    assert draft_transfer.last_billed_at == start + timedelta(hours=24)


def test_meter_transfer_is_idempotent_when_rerun_for_the_same_due_period(
    draft_transfer, ft_settings
):
    """The scheduler is at-least-once: if the process died right after this committed but before
    the job's lease was released, the same tick could run `meter_transfer` on this transfer
    again. A rerun right away must not charge a second time -- `last_billed_at` has already
    advanced past the 24h due threshold from the first call."""
    ft_settings.price_per_gb_per_day = Decimal('1.0')
    ft_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    balance_before = get_balance(draft_transfer.owner)

    metering.meter_transfer(draft_transfer, ft_settings)
    metering.meter_transfer(draft_transfer, ft_settings)

    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 1
    assert draft_transfer.credits_charged == 1
    assert get_balance(draft_transfer.owner) == balance_before - 1


def test_meter_transfer_suspends_when_insufficient_credits(draft_transfer, ft_settings):
    ft_settings.price_per_gb_per_day = Decimal('1.0')
    ft_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    metering.meter_transfer(draft_transfer, ft_settings)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED
    assert draft_transfer.suspended_at is not None


def test_meter_transfer_ignores_non_active_or_ownerless(draft_transfer, ft_settings):
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    draft_transfer.status = TransferStatus.DISABLED
    draft_transfer.save()

    metering.meter_transfer(draft_transfer, ft_settings)
    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 0


def test_reenable_suspended_transfers_for_user_requires_balance(draft_transfer):
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now()
    draft_transfer.save()
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    count = metering.reenable_suspended_transfers_for_user(draft_transfer.owner)

    assert count == 0
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED


def test_reenable_suspended_transfers_for_user_resets_billing_clock(draft_transfer):
    _make_active(
        draft_transfer, size_bytes=_ONE_GB, last_billed_at=timezone.now() - timedelta(days=5)
    )
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now() - timedelta(days=1)
    draft_transfer.save()

    count = metering.reenable_suspended_transfers_for_user(draft_transfer.owner)

    assert count == 1
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.suspended_at is None
    assert draft_transfer.last_billed_at > timezone.now() - timedelta(minutes=1)


def test_delete_files_past_grace_period(draft_transfer, uploaded_file, fake_storage, ft_settings):
    ft_settings.suspension_grace_days = 7
    ft_settings.save()
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now() - timedelta(days=8)
    draft_transfer.save()

    count = metering.delete_files_past_grace_period(ft_settings)

    assert count == 1
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert uploaded_file.storage_key not in fake_storage.objects


def test_delete_files_past_grace_period_leaves_recent_suspensions_alone(
    draft_transfer, ft_settings
):
    ft_settings.suspension_grace_days = 7
    ft_settings.save()
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now() - timedelta(days=1)
    draft_transfer.save()

    count = metering.delete_files_past_grace_period(ft_settings)

    assert count == 0
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED
