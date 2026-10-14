"""Every status change that ends or aborts a transfer is a conditional write (audit #16).

Each test holds a *stale* instance (read while the transfer was in one state), changes the row in
the database underneath it the way a concurrent request or job would, and then calls the service:
the newer state must win, and the loser must not delete files or send anything.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from .. import jobs
from ..models import AbuseReportReason, AbuseReportStatus, Transfer, TransferStatus
from ..services import actions, lifecycle, metering, takedown, uploads
from ..services.reports import create_report

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _set(transfer, **fields):
    """Write `fields` to the row only, leaving `transfer` (the caller's instance) stale."""
    Transfer.objects.filter(pk=transfer.pk).update(**fields)


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


@pytest.fixture
def notified(monkeypatch):
    """Records the transfers `end_transfer` would have emailed a "files deleted" notice for."""
    calls = []
    monkeypatch.setattr(lifecycle, '_notify_files_deleted', lambda transfer: calls.append(transfer))
    return calls


# --- end_transfer -----------------------------------------------------------------------------


def test_end_transfer_wins_from_a_live_status(
    draft_transfer, uploaded_file, fake_storage, notified
):
    _active(draft_transfer)
    hook_calls = []

    won = lifecycle.end_transfer(
        draft_transfer,
        TransferStatus.EXPIRED,
        notify=True,
        before_delete=lambda: hook_calls.append(1),
    )

    assert won is True
    assert hook_calls == [1]
    assert len(notified) == 1
    assert draft_transfer.status == TransferStatus.EXPIRED
    assert draft_transfer.ended_at is not None
    assert draft_transfer.files_deleted_at is not None
    assert uploaded_file.storage_key not in fake_storage.objects


@pytest.mark.parametrize(
    'newer', [TransferStatus.EXPIRED, TransferStatus.DELETED, TransferStatus.TAKEN_DOWN]
)
def test_end_transfer_does_not_overwrite_an_already_ended_transfer(
    draft_transfer, uploaded_file, fake_storage, notified, newer
):
    _active(draft_transfer)
    ended_at = timezone.now() - timedelta(hours=1)
    _set(draft_transfer, status=newer, ended_at=ended_at)
    hook_calls = []

    won = lifecycle.end_transfer(
        draft_transfer,
        TransferStatus.DELETED,
        notify=True,
        before_delete=lambda: hook_calls.append(1),
    )

    assert won is False
    assert hook_calls == []
    assert notified == []
    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == newer
    assert draft_transfer.ended_at == ended_at
    assert draft_transfer.files_deleted_at is None


def test_end_transfer_respects_from_statuses(draft_transfer, uploaded_file, fake_storage, notified):
    _active(draft_transfer, status=TransferStatus.SUSPENDED, suspended_at=timezone.now())
    _set(draft_transfer, status=TransferStatus.ACTIVE, suspended_at=None)

    won = lifecycle.end_transfer(
        draft_transfer, TransferStatus.DELETED, from_statuses=(TransferStatus.SUSPENDED,)
    )

    assert won is False
    assert notified == []
    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


# --- grace-period job -------------------------------------------------------------------------


def test_grace_period_job_skips_a_transfer_a_top_up_re_enabled(
    draft_transfer, uploaded_file, fake_storage, ft_settings, notified, monkeypatch
):
    ft_settings.suspension_grace_days = 7
    ft_settings.save()
    _active(
        draft_transfer,
        status=TransferStatus.SUSPENDED,
        suspended_at=timezone.now() - timedelta(days=8),
    )
    real = metering.end_transfer

    def _re_enabled_meanwhile(transfer, *args, **kwargs):
        _set(transfer, status=TransferStatus.ACTIVE, suspended_at=None)
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(metering, 'end_transfer', _re_enabled_meanwhile)

    assert metering.delete_files_past_grace_period(ft_settings) == 0

    assert notified == []
    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.deleted_at is None


# --- expiry job -------------------------------------------------------------------------------


def test_expire_job_skips_a_transfer_ended_by_someone_else_meanwhile(
    draft_transfer, uploaded_file, fake_storage, notified, monkeypatch
):
    _active(draft_transfer, expires_at=timezone.now() - timedelta(minutes=1))
    real = jobs.end_transfer

    def _taken_down_meanwhile(transfer, *args, **kwargs):
        _set(transfer, status=TransferStatus.TAKEN_DOWN, ended_at=timezone.now())
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(jobs, 'end_transfer', _taken_down_meanwhile)

    jobs.expire_transfers()

    assert notified == []
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.TAKEN_DOWN


# --- dashboard / API delete now ---------------------------------------------------------------


def test_delete_now_refuses_a_transfer_that_ended_meanwhile(
    draft_transfer, uploaded_file, fake_storage, notified
):
    _active(draft_transfer)
    _set(draft_transfer, status=TransferStatus.EXPIRED, ended_at=timezone.now())

    with pytest.raises(ValidationError):
        actions.delete_transfer_now(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED
    assert draft_transfer.deleted_at is None


# --- disable ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    'newer', [TransferStatus.EXPIRED, TransferStatus.DELETED, TransferStatus.TAKEN_DOWN]
)
def test_disable_does_not_resurrect_a_transfer_that_ended_meanwhile(draft_transfer, newer):
    _active(draft_transfer)
    _set(draft_transfer, status=newer)

    with pytest.raises(ValidationError):
        actions.disable_transfer(draft_transfer)

    assert draft_transfer.status == TransferStatus.ACTIVE  # the stale object is untouched
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == newer


def test_disable_is_still_allowed_while_held_for_review(draft_transfer):
    _active(draft_transfer, held_for_review_at=timezone.now())

    actions.disable_transfer(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DISABLED


# --- abort_draft / cleanup_drafts -------------------------------------------------------------


@pytest.mark.parametrize('newer', [TransferStatus.ACTIVE, TransferStatus.DELETED])
def test_abort_draft_leaves_a_draft_that_was_sent_meanwhile(
    draft_transfer, uploaded_file, fake_storage, newer
):
    _set(draft_transfer, status=newer, completed_at=timezone.now())

    assert uploads.abort_draft(draft_transfer, storage=fake_storage) is False

    assert uploaded_file.storage_key in fake_storage.objects
    assert draft_transfer.files.count() == 1
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == newer
    assert draft_transfer.deleted_at is None


def test_abort_draft_aborts_a_pending_confirmation_transfer(draft_transfer, fake_storage):
    _set(draft_transfer, status=TransferStatus.PENDING_CONFIRMATION)
    draft_transfer.refresh_from_db()

    assert uploads.abort_draft(draft_transfer, storage=fake_storage) is True

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED


def test_abort_draft_storage_failure_leaves_the_draft_for_a_retry(
    draft_transfer, uploaded_file, fake_storage, monkeypatch
):
    def _boom(*args, **kwargs):
        raise RuntimeError('S3 is down')

    monkeypatch.setattr(fake_storage, 'delete_prefix', _boom)

    with pytest.raises(RuntimeError):
        uploads.abort_draft(draft_transfer, storage=fake_storage)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT  # next cleanup_drafts run retries it
    assert draft_transfer.deleted_at is None


def test_cleanup_drafts_does_not_delete_a_draft_sent_during_the_run(
    draft_transfer, uploaded_file, fake_storage, monkeypatch
):
    Transfer.objects.filter(pk=draft_transfer.pk).update(
        created_at=timezone.now() - timedelta(hours=30)
    )
    real = jobs.abort_draft

    def _sent_meanwhile(transfer, *args, **kwargs):
        _set(transfer, status=TransferStatus.ACTIVE, completed_at=timezone.now())
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(jobs, 'abort_draft', _sent_meanwhile)

    jobs.cleanup_drafts()

    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


# --- takedown ---------------------------------------------------------------------------------


def test_takedown_of_a_transfer_that_ended_meanwhile_changes_nothing(
    draft_transfer, uploaded_file, fake_storage, admin_user, notified
):
    _active(draft_transfer)
    report = create_report(
        draft_transfer, reason=AbuseReportReason.MALWARE, reporter_ip='203.0.113.1'
    )
    _set(draft_transfer, status=TransferStatus.DELETED, deleted_at=timezone.now())

    won = takedown.take_down_transfer(draft_transfer, by=admin_user, reason='x', notify=True)

    assert won is False
    assert notified == []
    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert draft_transfer.taken_down_by_id is None
    report.refresh_from_db()
    assert report.status == AbuseReportStatus.PENDING
