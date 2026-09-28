"""Auto-hold a transfer after N pending abuse reports from distinct IPs (issue #59, optional --
`FileTransferSettings.auto_hold_report_threshold`). Deliberately not `TransferStatus.SUSPENDED`:
these tests pin down that a held transfer survives both the `credits_added` signal and the
suspension grace-period job untouched, and isn't billed while held.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing.services import add_credits, get_balance, spend_credits

from .. import jobs
from ..models import AbuseReportReason, TransferStatus
from ..services.hold import maybe_hold_for_reports, release_hold
from ..services.metering import meter_transfer
from ..services.reports import create_report

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

_ONE_GB = 1024**3


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def test_off_by_default(draft_transfer, ft_settings):
    _active(draft_transfer)
    assert ft_settings.auto_hold_report_threshold == 0

    for ip in ('203.0.113.1', '203.0.113.2', '203.0.113.3'):
        create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip=ip)

    draft_transfer.refresh_from_db()
    assert draft_transfer.held_for_review_at is None


def test_holds_at_threshold(draft_transfer, ft_settings):
    ft_settings.auto_hold_report_threshold = 3
    ft_settings.save()
    _active(draft_transfer)

    for ip in ('203.0.113.1', '203.0.113.2'):
        create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip=ip)
    draft_transfer.refresh_from_db()
    assert draft_transfer.held_for_review_at is None

    create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.3')
    draft_transfer.refresh_from_db()
    assert draft_transfer.held_for_review_at is not None


def test_requires_distinct_ips_not_just_report_count(draft_transfer, ft_settings):
    """Several reports from the *same* IP never trigger the hold on their own -- see
    `create_report`'s own dedupe window, which this relies on to even get repeat rows in."""
    ft_settings.auto_hold_report_threshold = 2
    ft_settings.save()
    _active(draft_transfer)

    create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.1')
    assert maybe_hold_for_reports(draft_transfer, ft_settings) is False


def test_held_transfer_is_unavailable(client, draft_transfer, uploaded_file):
    _active(draft_transfer, held_for_review_at=timezone.now())

    response = client.get(reverse('t:download', args=[draft_transfer.slug]))

    assert response.status_code == 404


def test_held_transfer_is_not_billed(draft_transfer, ft_settings):
    _active(
        draft_transfer,
        size_bytes=_ONE_GB,
        last_billed_at=timezone.now() - timedelta(hours=25),
        held_for_review_at=timezone.now(),
    )
    ft_settings.price_per_gb_per_day = Decimal('1.0')
    ft_settings.save()
    balance_before = get_balance(draft_transfer.owner)

    meter_transfer(draft_transfer, ft_settings)

    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 0
    assert get_balance(draft_transfer.owner) == balance_before


def test_meter_transfers_job_skips_held_transfers(draft_transfer, ft_settings):
    _active(
        draft_transfer,
        size_bytes=_ONE_GB,
        last_billed_at=timezone.now() - timedelta(hours=25),
        held_for_review_at=timezone.now(),
    )

    jobs.meter_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 0


def test_hold_not_released_by_credits_added_signal(
    draft_transfer, ft_settings, django_capture_on_commit_callbacks
):
    """A held transfer's status stays whatever it already was (normally `ACTIVE`) -- the
    `credits_added` signal only re-enables `SUSPENDED` transfers, so it must never touch the
    hold."""
    _active(draft_transfer, held_for_review_at=timezone.now())

    with django_capture_on_commit_callbacks(execute=True):
        add_credits(draft_transfer.owner, 10, description='top up', source='test')

    draft_transfer.refresh_from_db()
    assert draft_transfer.held_for_review_at is not None


def test_hold_not_deleted_by_grace_period_job(
    draft_transfer, uploaded_file, ft_settings, fake_storage
):
    """The suspension grace-period job only ever looks at `SUSPENDED` transfers -- a held
    transfer, still `ACTIVE` underneath, must never be swept up by it."""
    ft_settings.suspension_grace_days = 7
    ft_settings.save()
    _active(
        draft_transfer,
        held_for_review_at=timezone.now() - timedelta(days=30),
    )
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    jobs.meter_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.held_for_review_at is not None
    assert uploaded_file.storage_key in fake_storage.objects


def test_release_hold(draft_transfer):
    draft_transfer.held_for_review_at = timezone.now()
    draft_transfer.save()

    release_hold(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.held_for_review_at is None


def test_admin_release_hold_action(client, admin_user, draft_transfer, ft_settings):
    _active(draft_transfer, held_for_review_at=timezone.now())
    create_report(draft_transfer, reason=AbuseReportReason.OTHER, reporter_ip='203.0.113.1')
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'release_hold_action',
            '_selected_action': [str(draft_transfer.id)],
        },
        follow=True,
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.held_for_review_at is None
