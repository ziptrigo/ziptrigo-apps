from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import CreditTransaction
from apps.billing.services import (
    InsufficientCreditsError,
    add_credits,
    get_balance,
    spend_credits,
)
from apps.core.models import Notification
from apps.core.services import notifications

from ..models import Transfer, TransferStatus
from ..services import metering

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

_ONE_GB = 1024**3


def _make_active(transfer, *, size_bytes, last_billed_at=None):
    transfer.status = TransferStatus.ACTIVE
    transfer.size_bytes = size_bytes
    transfer.last_billed_at = last_billed_at or (timezone.now() - timedelta(hours=25))
    transfer.save()
    return transfer


def test_meter_transfer_skips_when_not_due(draft_transfer):
    _make_active(draft_transfer, size_bytes=_ONE_GB, last_billed_at=timezone.now())
    balance_before = get_balance(draft_transfer.owner)

    metering.meter_transfer(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 0
    assert get_balance(draft_transfer.owner) == balance_before


def test_meter_transfer_rounds_down_and_charges_every_third_day(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('0.4')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    balance_before = get_balance(draft_transfer.owner)

    # Day 1: accrues 0.4, nothing charged yet.
    metering.meter_transfer(draft_transfer)
    assert draft_transfer.billed_days == 1
    assert draft_transfer.accrued == Decimal('0.4')
    assert get_balance(draft_transfer.owner) == balance_before

    # Day 2: accrues to 0.8, still nothing charged.
    draft_transfer.last_billed_at = timezone.now() - timedelta(hours=25)
    draft_transfer.save()
    metering.meter_transfer(draft_transfer)
    assert draft_transfer.billed_days == 2
    assert draft_transfer.accrued == Decimal('0.8')
    assert get_balance(draft_transfer.owner) == balance_before

    # Day 3: accrues to 1.2 -- floor(1.2) = 1 credit spent, 0.2 remains.
    draft_transfer.last_billed_at = timezone.now() - timedelta(hours=25)
    draft_transfer.save()
    metering.meter_transfer(draft_transfer)
    assert draft_transfer.billed_days == 3
    assert draft_transfer.accrued == Decimal('0.2')
    assert draft_transfer.credits_charged == 1
    assert get_balance(draft_transfer.owner) == balance_before - 1

    tx = CreditTransaction.objects.filter(user=draft_transfer.owner, source='file_transfer').get()
    assert tx.amount == -1
    assert 'day 3' in tx.description
    assert draft_transfer.display_name in tx.description or 'Untitled transfer' in tx.description


def test_meter_transfer_advances_last_billed_at_by_exactly_one_period(draft_transfer):
    """Advancing to `last_billed_at + 24h` rather than to `now` matters at the margins: a tick
    that lands a little early each day would otherwise never quite reach the 24h mark and skip a
    day's charge (see the docstring on `meter_transfer`)."""
    start = timezone.now() - timedelta(hours=25)
    _make_active(draft_transfer, size_bytes=_ONE_GB, last_billed_at=start)

    metering.meter_transfer(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.last_billed_at == start + timedelta(hours=24)


def test_meter_transfer_is_idempotent_when_rerun_for_the_same_due_period(
    draft_transfer, billing_settings
):
    """The scheduler is at-least-once: if the process died right after this committed but before
    the job's lease was released, the same tick could run `meter_transfer` on this transfer
    again. A rerun right away must not charge a second time -- `last_billed_at` has already
    advanced past the 24h due threshold from the first call."""
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    balance_before = get_balance(draft_transfer.owner)

    metering.meter_transfer(draft_transfer)
    metering.meter_transfer(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 1
    assert draft_transfer.credits_charged == 1
    assert get_balance(draft_transfer.owner) == balance_before - 1


def test_meter_transfer_suspends_when_insufficient_credits(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    metering.meter_transfer(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED
    assert draft_transfer.suspended_at is not None


def test_suspending_notifies_the_owner_in_the_app(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    metering.meter_transfer(draft_transfer)

    notification = Notification.objects.get(user=draft_transfer.owner)
    assert notification.source == 'file_transfer'
    assert notification.title.startswith('Transfer suspended: ')
    assert notification.url == reverse('file_transfer:dashboard')
    assert 'ran out of credits' in notification.body


def test_the_suspension_notice_says_how_many_credits_are_needed(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('3.0')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    metering.meter_transfer(draft_transfer)

    body = Notification.objects.get(user=draft_transfer.owner).body
    assert 'at least 3 credits' in body
    assert 'smaller top-up will not resume' in body


def test_the_suspension_notice_is_keyed_by_its_transfer(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    metering.meter_transfer(draft_transfer)

    notification = Notification.objects.get(user=draft_transfer.owner)
    assert notification.key == f'transfer_suspended:{draft_transfer.pk}'


def test_the_notice_uses_the_price_and_grace_period_it_is_given(
    draft_transfer, billing_settings, monkeypatch
):
    """`meter_transfers` loads both once per run and passes them, so the hook queries neither."""
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    def must_not_load(*args, **kwargs):
        raise AssertionError('loaded again')

    monkeypatch.setattr(metering.FileTransferSettings, 'load', must_not_load)
    monkeypatch.setattr(metering, 'get_storage_price_per_gb_per_day', must_not_load)

    metering.meter_transfer(draft_transfer, Decimal('1.0'), 9)

    body = Notification.objects.get(user=draft_transfer.owner).body
    assert 'within 9 day(s)' in body
    assert 'at least 1 credit ' in body


def test_the_daily_job_passes_its_settings_to_the_notice(draft_transfer, billing_settings):
    from .. import jobs
    from ..models import FileTransferSettings

    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    row = FileTransferSettings.load()
    row.suspension_grace_days = 12
    row.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    jobs.meter_transfers()

    assert 'within 12 day(s)' in Notification.objects.get(user=draft_transfer.owner).body


def test_a_hook_that_fails_midway_leaves_nothing_behind_and_keeps_the_suspension(
    draft_transfer, billing_settings, monkeypatch
):
    """The hook runs in its own savepoint: whatever it wrote before failing is rolled back, and
    the surrounding transaction (which holds the suspension) is still usable and commits."""

    def write_then_fail(user, **kwargs):
        Notification.objects.create(user_id=user, source='x', title='half-written')
        raise RuntimeError('boom')

    monkeypatch.setattr(
        'apps.file_transfer.services.metering.notifications.notify', write_then_fail
    )
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    metering.meter_transfer(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED
    assert not Notification.objects.exists()


def test_a_paid_day_creates_no_notification(draft_transfer):
    _make_active(draft_transfer, size_bytes=_ONE_GB)

    metering.meter_transfer(draft_transfer)

    assert not Notification.objects.exists()


def test_a_failing_notification_does_not_stop_the_suspension(
    draft_transfer, billing_settings, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('apps.file_transfer.services.metering.notifications.notify', boom)
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    metering.meter_transfer(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED


def test_meter_transfer_ignores_non_active_or_ownerless(draft_transfer):
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    draft_transfer.status = TransferStatus.DISABLED
    draft_transfer.save()

    metering.meter_transfer(draft_transfer)
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


def test_meter_transfer_uses_the_price_it_is_given(draft_transfer, billing_settings):
    """A caller metering many transfers (the job) loads the price once and passes it in; that
    value is used rather than re-reading `BillingSettings` per transfer."""
    billing_settings.storage_price_per_gb_per_day = Decimal('9')
    billing_settings.save()
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    balance_before = get_balance(draft_transfer.owner)

    metering.meter_transfer(draft_transfer, Decimal('2'))

    draft_transfer.refresh_from_db()
    assert draft_transfer.credits_charged == 2
    assert draft_transfer.accrued == Decimal('0')
    assert get_balance(draft_transfer.owner) == balance_before - 2


def _suspend(transfer, *, size_bytes, suspended_at=None):
    _make_active(transfer, size_bytes=size_bytes)
    transfer.status = TransferStatus.SUSPENDED
    transfer.suspended_at = suspended_at or timezone.now()
    transfer.save()
    return transfer


def test_daily_cost_rounds_up_with_a_minimum_of_one(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.5')
    billing_settings.save()
    draft_transfer.size_bytes = _ONE_GB
    assert metering.daily_cost(draft_transfer) == 2
    draft_transfer.size_bytes = 0
    assert metering.daily_cost(draft_transfer) == 1


def test_reenable_requires_balance_to_cover_the_transfers_day(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('3.0')
    billing_settings.save()
    _suspend(draft_transfer, size_bytes=_ONE_GB)
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner) - 2, source='test')

    assert metering.reenable_suspended_transfers_for_user(draft_transfer.owner) == 0
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED

    add_credits(draft_transfer.owner, 1, description='top up', source='test')
    assert metering.reenable_suspended_transfers_for_user(draft_transfer.owner) == 1
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_reenable_takes_oldest_first_while_the_balance_covers_the_running_sum(
    draft_transfer, billing_settings
):
    billing_settings.storage_price_per_gb_per_day = Decimal('2.0')
    billing_settings.save()
    older = _suspend(
        draft_transfer, size_bytes=_ONE_GB, suspended_at=timezone.now() - timedelta(days=2)
    )
    newer = Transfer.objects.create(
        owner=older.owner,
        status=TransferStatus.SUSPENDED,
        size_bytes=_ONE_GB,
        suspended_at=timezone.now() - timedelta(days=1),
    )
    spend_credits(older.owner, get_balance(older.owner) - 3, source='test')

    # 3 credits cover the older one's 2-credit day, but not the newer one's on top of it.
    assert metering.reenable_suspended_transfers_for_user(older.owner) == 1
    older.refresh_from_db()
    newer.refresh_from_db()
    assert older.status == TransferStatus.ACTIVE
    assert newer.status == TransferStatus.SUSPENDED


def test_reenable_limited_to_given_transfer_ids(draft_transfer):
    _suspend(draft_transfer, size_bytes=_ONE_GB)

    assert metering.reenable_suspended_transfers_for_user(draft_transfer.owner, []) == 0
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED


def test_disable_then_reenable_before_the_day_is_due_is_still_charged(
    draft_transfer, billing_settings, monkeypatch
):
    """Re-enabling a disabled transfer used to reset `last_billed_at` to now, so disabling and
    re-enabling inside every 24h meant the day never fell due (free storage)."""
    from ..services import actions

    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    started = timezone.now()
    last_billed_at = started - timedelta(hours=20)
    _make_active(draft_transfer, size_bytes=_ONE_GB, last_billed_at=last_billed_at)
    balance_before = get_balance(draft_transfer.owner)

    actions.disable_transfer(draft_transfer)
    actions.reenable_transfer_action(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.last_billed_at == last_billed_at

    # Not due yet...
    metering.meter_transfer(draft_transfer)
    assert get_balance(draft_transfer.owner) == balance_before
    # ...but due 4h from now, however many times it was toggled in between.
    monkeypatch.setattr(timezone, 'now', lambda: started + timedelta(hours=5))
    metering.meter_transfer(draft_transfer)
    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 1
    assert draft_transfer.credits_charged == 1
    assert get_balance(draft_transfer.owner) == balance_before - 1


def test_reenabling_after_a_long_disabled_stretch_charges_one_day_not_a_backlog(
    draft_transfer, billing_settings
):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    _make_active(
        draft_transfer, size_bytes=_ONE_GB, last_billed_at=timezone.now() - timedelta(days=5)
    )
    draft_transfer.status = TransferStatus.DISABLED
    draft_transfer.save()

    assert metering.reenable_transfer(draft_transfer) is True

    draft_transfer.refresh_from_db()
    assert draft_transfer.last_billed_at > timezone.now() - timedelta(hours=24, minutes=1)
    metering.meter_transfer(draft_transfer)  # due immediately: one day...
    metering.meter_transfer(draft_transfer)  # ...and not a second one
    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 1


def test_reenable_transfer_does_not_resurrect_an_ended_transfer(draft_transfer):
    """The grace-period job or delete-now can end the row between the caller's read and the write:
    the conditional update must match nothing, and report that."""
    _suspend(draft_transfer, size_bytes=_ONE_GB)
    Transfer.objects.filter(pk=draft_transfer.pk).update(status=TransferStatus.DELETED)

    assert metering.reenable_transfer(draft_transfer) is False

    assert draft_transfer.status == TransferStatus.SUSPENDED  # the stale object is untouched
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert draft_transfer.last_billed_at < timezone.now() - timedelta(hours=1)


def test_bulk_reenable_does_not_count_a_transfer_deleted_before_the_write(
    draft_transfer, monkeypatch
):
    _suspend(draft_transfer, size_bytes=_ONE_GB)
    real = metering.reenable_transfer

    def _deleted_meanwhile(transfer):
        Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DELETED)
        return real(transfer)

    monkeypatch.setattr(metering, 'reenable_transfer', _deleted_meanwhile)

    assert metering.reenable_suspended_transfers_for_user(draft_transfer.owner) == 0
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED


def test_held_transfer_does_not_use_up_the_balance_other_transfers_need(
    draft_transfer, billing_settings
):
    billing_settings.storage_price_per_gb_per_day = Decimal('2.0')
    billing_settings.save()
    held = _suspend(
        draft_transfer, size_bytes=_ONE_GB, suspended_at=timezone.now() - timedelta(days=2)
    )
    held.held_for_review_at = timezone.now()
    held.save()
    other = Transfer.objects.create(
        owner=held.owner,
        status=TransferStatus.SUSPENDED,
        size_bytes=_ONE_GB,
        suspended_at=timezone.now() - timedelta(days=1),
    )
    spend_credits(held.owner, get_balance(held.owner) - 2, source='test')

    # Oldest first: the held one is re-enabled (still held, so unavailable and unbilled) without
    # reserving its 2 credits, which leaves the other one's day covered.
    assert metering.reenable_suspended_transfers_for_user(held.owner) == 2
    other.refresh_from_db()
    assert other.status == TransferStatus.ACTIVE


def test_suspended_email_says_how_many_credits_are_needed(
    draft_transfer, billing_settings, monkeypatch
):
    from ..services import emails

    billing_settings.storage_price_per_gb_per_day = Decimal('3.0')
    billing_settings.save()
    sent = []
    monkeypatch.setattr(emails, 'send_email', lambda **kwargs: sent.append(kwargs) or (1, 0))
    _suspend(draft_transfer, size_bytes=_ONE_GB)

    emails.send_suspended_notification.call(str(draft_transfer.id))

    assert len(sent) == 1
    assert 'at least 3 credits' in sent[0]['text_body']
    assert 'smaller top-up will not resume' in sent[0]['text_body']


def _suspended_with_notice(transfer, *, size_bytes=_ONE_GB):
    """Suspend `transfer` through metering, so it has its real, keyed notice."""
    _make_active(transfer, size_bytes=size_bytes)
    with mock.patch.object(metering, 'spend_credits', side_effect=InsufficientCreditsError('no')):
        metering.meter_transfer(transfer)
    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.SUSPENDED
    return transfer


def test_reenabling_deletes_that_transfers_suspended_notice_only(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    first = _suspended_with_notice(draft_transfer)
    second = Transfer.objects.create(owner=first.owner, status=TransferStatus.ACTIVE)
    second = _suspended_with_notice(second)
    assert Notification.objects.filter(user=first.owner).count() == 2

    assert metering.reenable_transfer(first) is True

    remaining = Notification.objects.get(user=first.owner)
    assert remaining.key == f'transfer_suspended:{second.pk}'


def test_a_top_up_that_resumes_a_transfer_clears_its_notice(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    transfer = _suspended_with_notice(draft_transfer)

    assert metering.reenable_suspended_transfers_for_user(transfer.owner) == 1

    assert not Notification.objects.filter(user=transfer.owner).exists()


def test_a_transfer_that_stays_suspended_keeps_its_notice(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('3.0')
    billing_settings.save()
    transfer = _suspended_with_notice(draft_transfer)
    spend_credits(transfer.owner, get_balance(transfer.owner) - 2, source='test')

    assert metering.reenable_suspended_transfers_for_user(transfer.owner) == 0

    assert Notification.objects.filter(user=transfer.owner).count() == 1


def test_a_lost_reenable_race_keeps_the_notice(draft_transfer, billing_settings):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    transfer = _suspended_with_notice(draft_transfer)
    Transfer.objects.filter(pk=transfer.pk).update(status=TransferStatus.DELETED)

    assert metering.reenable_transfer(transfer) is False

    assert Notification.objects.filter(user=transfer.owner).count() == 1


def test_the_dashboard_reenable_action_clears_the_notice(draft_transfer, billing_settings):
    from ..services import actions

    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    transfer = _suspended_with_notice(draft_transfer)

    actions.reenable_transfer_action(transfer)

    assert not Notification.objects.filter(user=transfer.owner).exists()


def test_a_failing_cleanup_never_undoes_the_resume(draft_transfer, billing_settings, monkeypatch):
    billing_settings.storage_price_per_gb_per_day = Decimal('1.0')
    billing_settings.save()
    transfer = _suspended_with_notice(draft_transfer)

    def boom(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr('apps.file_transfer.services.metering.notifications.delete_for_key', boom)

    assert metering.reenable_transfer(transfer) is True

    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.ACTIVE
    assert Notification.objects.filter(user=transfer.owner).count() == 1  # left for the user


def test_reenabling_a_disabled_transfer_leaves_other_notices_alone(draft_transfer):
    _make_active(draft_transfer, size_bytes=_ONE_GB)
    draft_transfer.status = TransferStatus.DISABLED
    draft_transfer.save()
    notifications.notify(
        draft_transfer.owner,
        source='file_transfer',
        title='x',
        key=metering.suspended_notice_key(draft_transfer.pk),
    )

    assert metering.reenable_transfer(draft_transfer) is True

    assert Notification.objects.filter(user=draft_transfer.owner).count() == 1
