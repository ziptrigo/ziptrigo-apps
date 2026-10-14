"""Every status change that ends or aborts a transfer is a conditional write (audit #16).

Each test holds a *stale* instance (read while the transfer was in one state), changes the row in
the database underneath it the way a concurrent request or job would, and then calls the service:
the newer state must win, and the loser must not delete files or send anything.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tokens import CustomAccessToken

from .. import jobs, services
from ..models import (
    AbuseReportReason,
    AbuseReportStatus,
    DownloadEvent,
    FileTransferSettings,
    Transfer,
    TransferStatus,
)
from ..services import actions, anonymous, lifecycle, metering, takedown, uploads
from ..services.actions import TransferHeldError
from ..services.reports import create_report
from ..services.send import SendOptions, finalize_send

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


def test_end_transfer_respects_only_if(draft_transfer, uploaded_file, fake_storage, notified):
    _active(draft_transfer)

    won = lifecycle.end_transfer(
        draft_transfer, TransferStatus.EXPIRED, notify=True, only_if=Q(max_downloads__isnull=False)
    )

    assert won is False
    assert notified == []
    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.ended_at is None

    assert lifecycle.end_transfer(
        draft_transfer, TransferStatus.EXPIRED, only_if=Q(max_downloads__isnull=True)
    )


def test_a_per_key_s3_delete_error_leaves_the_files_for_the_sweep(draft_transfer, uploaded_file):
    """`DeleteObjects` answers 200 with an `Errors` list; that must not read as "deleted"."""
    from unittest.mock import MagicMock

    from apps.core.services.storage import S3Storage

    _active(draft_transfer)
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {'Contents': [{'Key': uploaded_file.storage_key}]}
    ]
    client.delete_objects.return_value = {
        'Errors': [{'Key': uploaded_file.storage_key, 'Code': 'AccessDenied'}]
    }
    storage = S3Storage(bucket='b', client=client)

    with pytest.raises(RuntimeError, match='AccessDenied'):
        lifecycle.end_transfer(draft_transfer, TransferStatus.EXPIRED, storage=storage)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED
    assert draft_transfer.files_deleted_at is None  # expire_transfers' sweep retries it


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


def test_grace_period_job_skips_a_transfer_suspended_again_meanwhile(
    draft_transfer, uploaded_file, fake_storage, ft_settings, notified, monkeypatch
):
    """Re-enabled and suspended again after the query: still `SUSPENDED`, but a fresh grace period
    has just started, so the stale `suspended_at` must not condemn it."""
    ft_settings.suspension_grace_days = 7
    ft_settings.save()
    _active(
        draft_transfer,
        status=TransferStatus.SUSPENDED,
        suspended_at=timezone.now() - timedelta(days=8),
    )
    real = metering.end_transfer

    def _suspended_again_meanwhile(transfer, *args, **kwargs):
        _set(transfer, suspended_at=timezone.now())
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(metering, 'end_transfer', _suspended_again_meanwhile)

    assert metering.delete_files_past_grace_period(ft_settings) == 0

    assert notified == []
    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED
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


def test_expire_job_skips_a_transfer_the_owner_extended_meanwhile(
    draft_transfer, uploaded_file, fake_storage, notified, monkeypatch
):
    _active(draft_transfer, expires_at=timezone.now() - timedelta(minutes=1))
    real = jobs.end_transfer

    def _extended_meanwhile(transfer, *args, **kwargs):
        _set(transfer, expires_at=timezone.now() + timedelta(days=3))
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(jobs, 'end_transfer', _extended_meanwhile)

    jobs.expire_transfers()

    assert notified == []
    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.ended_at is None


# --- max-downloads fallback ---


def _at_download_limit(transfer):
    _active(transfer, max_downloads=1)
    DownloadEvent.objects.create(transfer=transfer, file=None, ip='203.0.113.1')


def test_max_downloads_fallback_ends_a_transfer_at_its_limit(
    draft_transfer, uploaded_file, fake_storage
):
    _at_download_limit(draft_transfer)

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED
    # Deletion is deferred until the presigned URL it may have handed out has expired.
    assert uploaded_file.storage_key in fake_storage.objects


def test_max_downloads_fallback_skips_a_transfer_disabled_meanwhile(
    draft_transfer, uploaded_file, fake_storage, monkeypatch
):
    _at_download_limit(draft_transfer)
    real = jobs.end_transfer

    def _disabled_meanwhile(transfer, *args, **kwargs):
        _set(transfer, status=TransferStatus.DISABLED)
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(jobs, 'end_transfer', _disabled_meanwhile)

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DISABLED
    assert draft_transfer.ended_at is None


def test_max_downloads_fallback_skips_a_transfer_whose_limit_was_raised_meanwhile(
    draft_transfer, uploaded_file, fake_storage, monkeypatch
):
    _at_download_limit(draft_transfer)
    real = jobs.end_transfer

    def _raised_meanwhile(transfer, *args, **kwargs):
        _set(transfer, max_downloads=5)
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(jobs, 'end_transfer', _raised_meanwhile)

    jobs.expire_transfers()

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.ended_at is None


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


def test_delete_now_refuses_a_transfer_put_on_hold_meanwhile(
    draft_transfer, uploaded_file, fake_storage, notified
):
    """A hold applied after the instance was read must still freeze the transfer."""
    _active(draft_transfer)
    _set(draft_transfer, held_for_review_at=timezone.now())

    with pytest.raises(TransferHeldError):
        actions.delete_transfer_now(draft_transfer)

    assert uploaded_file.storage_key in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.deleted_at is None


def test_delete_now_losing_the_claim_refreshes_the_instance(draft_transfer, uploaded_file):
    _active(draft_transfer)
    _set(draft_transfer, status=TransferStatus.EXPIRED, ended_at=timezone.now())

    with pytest.raises(ValidationError):
        actions.delete_transfer_now(draft_transfer)

    assert draft_transfer.status == TransferStatus.EXPIRED  # the state that won


# --- disable ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    'newer', [TransferStatus.EXPIRED, TransferStatus.DELETED, TransferStatus.TAKEN_DOWN]
)
def test_disable_does_not_resurrect_a_transfer_that_ended_meanwhile(draft_transfer, newer):
    _active(draft_transfer)
    _set(draft_transfer, status=newer)

    with pytest.raises(ValidationError):
        actions.disable_transfer(draft_transfer)

    # The instance now shows the state that won, so the row a view renders next to the error
    # does not still say Active.
    assert draft_transfer.status == newer
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


# --- the losing path shows the state that won (dashboard views) -------------------------------

HX_HEADERS = {'HTTP_HX_REQUEST': 'true'}


@pytest.mark.parametrize(
    ('url_name', 'service'),
    [
        ('file_transfer:disable', 'disable_transfer'),
        ('file_transfer:delete', 'delete_transfer_now'),
    ],
)
def test_dashboard_action_that_loses_renders_the_row_in_its_new_state(
    client, draft_transfer, uploaded_file, monkeypatch, url_name, service
):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)
    real = getattr(services, service)

    def _expired_meanwhile(transfer, *args, **kwargs):
        # After the view read the transfer, before the service's conditional write.
        _set(transfer, status=TransferStatus.EXPIRED, ended_at=timezone.now())
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(services, service, _expired_meanwhile)

    response = client.post(reverse(url_name, args=[draft_transfer.id]), **HX_HEADERS)

    assert response.status_code == 422
    content = response.content.decode()
    assert 'font-semibold">Expired</span>' in content  # the state that won, not Active


# --- API: deleting a draft that changed under us ----------------------------------------------


@pytest.fixture
def api_headers(funded_user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(funded_user)}'}


def test_api_delete_draft_is_idempotent_when_cleanup_got_there_first(
    client, draft_transfer, uploaded_file, fake_storage, api_headers, monkeypatch
):
    real = services.abort_draft

    def _cleaned_up_meanwhile(transfer, *args, **kwargs):
        real(Transfer.objects.get(pk=transfer.pk), storage=fake_storage)  # cleanup_drafts won
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(services, 'abort_draft', _cleaned_up_meanwhile)
    delete_now = []
    monkeypatch.setattr(services, 'delete_transfer_now', lambda t: delete_now.append(t))

    response = client.delete(f'/api/ft/transfers/{draft_transfer.id}', **api_headers)

    assert response.status_code == 204
    assert delete_now == []
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED


def test_api_delete_draft_deletes_it_as_a_sent_transfer_when_sent_meanwhile(
    client, draft_transfer, uploaded_file, fake_storage, api_headers, monkeypatch
):
    real = services.abort_draft

    def _sent_meanwhile(transfer, *args, **kwargs):
        finalize_send(Transfer.objects.get(pk=transfer.pk), SendOptions(recipients=['a@b.com']))
        return real(transfer, *args, **kwargs)

    monkeypatch.setattr(services, 'abort_draft', _sent_meanwhile)

    response = client.delete(f'/api/ft/transfers/{draft_transfer.id}', **api_headers)

    assert response.status_code == 204
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert uploaded_file.storage_key not in fake_storage.objects


# --- anonymous activation failing while cleanup_drafts aborts the same transfer ---------------


def test_anonymous_activation_failure_racing_cleanup_still_raises_the_original_error(
    anon_enabled, fake_storage, monkeypatch
):
    transfer = Transfer.objects.create(
        owner=None,
        sender_email='sender@example.com',
        status=TransferStatus.PENDING_CONFIRMATION,
        sender_ip='203.0.113.7',
        anon_cookie_id='cookie-1',
    )
    settings_row = FileTransferSettings.load()
    settings_row.anonymous_max_transfers_per_ip_per_day = 0
    settings_row.save()
    real = anonymous.end_transfer

    def _cleaned_up_first(locked, *args, **kwargs):
        # `cleanup_drafts` aborts the unconfirmed transfer between the failed check and the end.
        assert uploads.abort_draft(Transfer.objects.get(pk=locked.pk), storage=fake_storage)
        return real(locked, *args, **kwargs)

    monkeypatch.setattr(anonymous, 'end_transfer', _cleaned_up_first)
    ended = []
    monkeypatch.setattr(lifecycle, '_notify_files_deleted', ended.append)

    with pytest.raises(ValidationError) as excinfo:
        anonymous._activate(transfer, 'sender@example.com', '203.0.113.7', 'cookie-1')

    assert 'no longer awaiting' not in str(excinfo.value)
    transfer.refresh_from_db()
    assert transfer.status == TransferStatus.DELETED
    assert ended == []


# --- admin: counts when a takedown loses ------------------------------------------------------


def _lose_first_takedown(monkeypatch):
    """Make the first `take_down_transfer` call find its transfer already ended."""
    real = takedown.take_down_transfer
    state = {'first': True}

    def _wrapper(transfer, **kwargs):
        if state['first']:
            state['first'] = False
            _set(transfer, status=TransferStatus.EXPIRED, ended_at=timezone.now())
        return real(transfer, **kwargs)

    monkeypatch.setattr(services, 'take_down_transfer', _wrapper)


def _messages(response) -> list[str]:
    return [str(m) for m in response.context['messages']]


def test_admin_takedown_message_counts_only_the_wins_and_reports_the_skipped(
    client, admin_user, draft_transfer, uploaded_file, monkeypatch
):
    _active(draft_transfer)
    other = Transfer.objects.create(owner=draft_transfer.owner, status=TransferStatus.ACTIVE)
    client.force_login(admin_user)
    _lose_first_takedown(monkeypatch)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'take_down_action',
            '_selected_action': [str(draft_transfer.id), str(other.id)],
            'apply': 'yes',
            'reason': 'abuse',
        },
        follow=True,
    )

    assert any(
        'Took down 1 transfer(s). 1 was skipped because it had already ended.' in m
        for m in _messages(response)
    )
    statuses = sorted(
        Transfer.objects.filter(pk__in=[draft_transfer.pk, other.pk]).values_list(
            'status', flat=True
        )
    )
    assert statuses == sorted([TransferStatus.EXPIRED, TransferStatus.TAKEN_DOWN])


def test_admin_block_sender_message_reports_takedowns_that_lost(
    client, admin_user, draft_transfer, uploaded_file, monkeypatch
):
    _active(draft_transfer)
    Transfer.objects.create(owner=draft_transfer.owner, status=TransferStatus.ACTIVE)
    client.force_login(admin_user)
    _lose_first_takedown(monkeypatch)

    response = client.post(
        reverse('custom_admin:file_transfer_transfer_changelist'),
        data={
            'action': 'block_sender_action',
            '_selected_action': [str(draft_transfer.id)],
            'apply': 'yes',
            'block_email': 'on',
            'reason': 'spam',
            'take_down_active_transfers': 'on',
        },
        follow=True,
    )

    assert any(
        'Took down 1 active transfer(s). 1 was skipped because it had already ended.' in m
        for m in _messages(response)
    )
