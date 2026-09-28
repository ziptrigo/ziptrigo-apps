from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.services import get_balance, spend_credits

from .. import jobs
from ..models import DownloadEvent, Transfer, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

_ONE_GB = 1024**3


def test_meter_transfers_charges_every_active_transfer(draft_transfer, ft_settings):
    ft_settings.price_per_gb_per_day = Decimal('1.0')
    ft_settings.save()
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.size_bytes = _ONE_GB
    draft_transfer.last_billed_at = timezone.now() - timedelta(hours=25)
    draft_transfer.save()
    balance_before = get_balance(draft_transfer.owner)

    jobs.meter_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 1
    assert get_balance(draft_transfer.owner) == balance_before - 1


def test_meter_transfers_reenables_topped_up_suspended_transfers(draft_transfer, ft_settings):
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now()
    draft_transfer.save()

    jobs.meter_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_meter_transfers_deletes_files_past_grace(
    draft_transfer, uploaded_file, fake_storage, ft_settings
):
    ft_settings.suspension_grace_days = 7
    ft_settings.save()
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now() - timedelta(days=8)
    draft_transfer.save()
    # Spend the balance so the fallback re-enable doesn't race the grace-period deletion.
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    jobs.meter_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert uploaded_file.storage_key not in fake_storage.objects


def test_expire_transfers_ends_past_expiry(draft_transfer, uploaded_file, fake_storage):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.expires_at = timezone.now() - timedelta(minutes=1)
    draft_transfer.save()

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED
    assert uploaded_file.storage_key not in fake_storage.objects


def test_expire_transfers_ends_transfers_past_max_downloads_as_fallback(
    draft_transfer, uploaded_file
):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.max_downloads = 1
    draft_transfer.save()
    DownloadEvent.objects.create(transfer=draft_transfer, file=uploaded_file)

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED


def test_expire_transfers_also_expires_disabled_transfers_past_expiry(draft_transfer):
    draft_transfer.status = TransferStatus.DISABLED
    draft_transfer.expires_at = timezone.now() - timedelta(minutes=1)
    draft_transfer.save()

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED


def test_expire_transfers_finishes_deferred_deletion_once_grace_window_passes(
    draft_transfer, uploaded_file, fake_storage
):
    """The last-download-dead-link fix: ending a transfer for reaching its download limit defers
    the actual S3 deletion (`Transfer.files_deleted_at` stays null) until it's safe -- this job
    sweeps those up once `GET_URL_EXPIRES_SECONDS` has passed since `ended_at`."""
    from ..services.storage import GET_URL_EXPIRES_SECONDS

    draft_transfer.status = TransferStatus.EXPIRED
    draft_transfer.ended_at = timezone.now() - timedelta(seconds=GET_URL_EXPIRES_SECONDS + 60)
    draft_transfer.save()

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.files_deleted_at is not None
    assert uploaded_file.storage_key not in fake_storage.objects


def test_expire_transfers_leaves_recently_ended_transfers_files_alone(
    draft_transfer, uploaded_file, fake_storage
):
    """The other half of the same fix: a transfer that only just ended must not have its files
    swept up before any presigned URL handed out for it could plausibly have expired."""
    draft_transfer.status = TransferStatus.EXPIRED
    draft_transfer.ended_at = timezone.now()
    draft_transfer.save()

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.files_deleted_at is None
    assert uploaded_file.storage_key in fake_storage.objects


def test_expire_transfers_sends_reminder_once(draft_transfer):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.expires_at = timezone.now() + timedelta(hours=12)
    draft_transfer.save()

    jobs.expire_transfers()
    draft_transfer.refresh_from_db()
    assert draft_transfer.expiry_notified_at is not None
    first_notified_at = draft_transfer.expiry_notified_at

    # Running again shouldn't re-notify (idempotent).
    jobs.expire_transfers()
    draft_transfer.refresh_from_db()
    assert draft_transfer.expiry_notified_at == first_notified_at


def test_expire_transfers_does_not_remind_far_future_expiry(draft_transfer):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.expires_at = timezone.now() + timedelta(days=10)
    draft_transfer.save()

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.expiry_notified_at is None


def test_expire_transfers_does_not_remind_a_transfer_that_only_ever_lived_a_day(draft_transfer):
    """A transfer sent with a 1-day expiry has nothing "tomorrow" to warn about -- it would fire
    the reminder on the very next tick after sending."""
    now = timezone.now()
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.completed_at = now
    draft_transfer.expires_at = now + timedelta(hours=23)
    draft_transfer.save()

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.expiry_notified_at is None


def test_expire_transfers_still_reminds_a_longer_transfer_close_to_expiry(draft_transfer):
    now = timezone.now()
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.completed_at = now - timedelta(days=29)
    draft_transfer.expires_at = now + timedelta(hours=12)
    draft_transfer.save()

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.expiry_notified_at is not None


def test_meter_transfers_continues_past_a_single_failing_transfer(
    draft_transfer, ft_settings, monkeypatch
):
    """One misbehaving transfer must not abort the whole job -- `last_started_at` is already
    advanced for the tick by the scheduler, so anything skipped this way waits a full interval."""
    from ..models import Transfer as TransferModel
    from ..services import metering

    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.last_billed_at = timezone.now() - timedelta(hours=25)
    draft_transfer.size_bytes = _ONE_GB
    draft_transfer.save()

    other = TransferModel.objects.create(
        owner=draft_transfer.owner,
        status=TransferStatus.ACTIVE,
        last_billed_at=timezone.now() - timedelta(hours=25),
        size_bytes=_ONE_GB,
        completed_at=timezone.now(),
    )

    original_meter_transfer = metering.meter_transfer

    def _boom(transfer, settings_row=None):
        if transfer.pk == draft_transfer.pk:
            raise RuntimeError('boom')
        return original_meter_transfer(transfer, settings_row)

    monkeypatch.setattr(jobs, 'meter_transfer', _boom)

    jobs.meter_transfers()

    other.refresh_from_db()
    assert other.billed_days == 1


def test_cleanup_drafts_removes_stale_drafts(draft_transfer, fake_storage):
    from ..services import uploads

    file = uploads.add_file(draft_transfer, 'a.bin', 10, storage=fake_storage)
    Transfer.objects.filter(pk=draft_transfer.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )

    jobs.cleanup_drafts()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert file.upload_id in fake_storage.aborted_uploads


def test_cleanup_drafts_leaves_recent_drafts_alone(draft_transfer):
    jobs.cleanup_drafts()
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


def test_purge_download_ips_nulls_old_ips(draft_transfer, uploaded_file):
    old_event = DownloadEvent.objects.create(
        transfer=draft_transfer, file=uploaded_file, ip='1.1.1.1'
    )
    DownloadEvent.objects.filter(pk=old_event.pk).update(
        created_at=timezone.now() - timedelta(days=91)
    )
    recent_event = DownloadEvent.objects.create(
        transfer=draft_transfer, file=uploaded_file, ip='2.2.2.2'
    )

    jobs.purge_download_ips()

    old_event.refresh_from_db()
    recent_event.refresh_from_db()
    assert old_event.ip is None
    assert recent_event.ip == '2.2.2.2'
