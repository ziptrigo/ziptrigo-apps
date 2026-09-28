"""Admin takedown of a transfer (issue #59): `services.takedown.take_down_transfer`, the public
download page afterwards, the owner's dashboard, and the JWT API.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from ..models import AbuseReportReason, AbuseReportStatus, TransferStatus, ZipStatus
from ..services.reports import create_report
from ..services.takedown import take_down_transfer
from ..services.zip import zip_key

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def test_take_down_transfer_sets_status_and_metadata(draft_transfer, uploaded_file, admin_user):
    _active(draft_transfer)

    take_down_transfer(draft_transfer, by=admin_user, reason='confirmed malware')

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.TAKEN_DOWN
    assert draft_transfer.taken_down_by_id == admin_user.id
    assert draft_transfer.taken_down_at is not None
    assert draft_transfer.takedown_reason == 'confirmed malware'
    assert draft_transfer.ended_at is not None


def test_take_down_transfer_deletes_s3_objects_including_zip(
    draft_transfer, uploaded_file, admin_user, fake_storage
):
    _active(draft_transfer)
    key = zip_key(draft_transfer.id)
    fake_storage.objects[key] = b'zip bytes'
    draft_transfer.zip_status = ZipStatus.READY
    draft_transfer.zip_key = key
    draft_transfer.save()

    take_down_transfer(draft_transfer, by=admin_user, reason='dmca')

    assert uploaded_file.storage_key not in fake_storage.objects
    assert key not in fake_storage.objects
    draft_transfer.refresh_from_db()
    assert draft_transfer.files_deleted_at is not None
    assert draft_transfer.zip_status == ZipStatus.NONE
    assert draft_transfer.zip_key == ''


def test_take_down_transfer_clears_hold(draft_transfer, uploaded_file, admin_user):
    _active(draft_transfer, held_for_review_at=timezone.now())

    take_down_transfer(draft_transfer, by=admin_user, reason='abuse')

    draft_transfer.refresh_from_db()
    assert draft_transfer.held_for_review_at is None


def test_take_down_transfer_stops_metering(draft_transfer, uploaded_file, admin_user):
    from apps.billing.services import get_balance

    from ..services.metering import meter_transfer

    _active(draft_transfer, size_bytes=1024**3, last_billed_at=timezone.now() - timedelta(hours=25))
    take_down_transfer(draft_transfer, by=admin_user, reason='abuse')
    balance_before = get_balance(draft_transfer.owner)

    meter_transfer(draft_transfer)

    draft_transfer.refresh_from_db()
    assert draft_transfer.billed_days == 0
    assert get_balance(draft_transfer.owner) == balance_before


def test_take_down_transfer_actions_pending_reports(draft_transfer, uploaded_file, admin_user):
    _active(draft_transfer)
    report = create_report(
        draft_transfer, reason=AbuseReportReason.MALWARE, reporter_ip='203.0.113.1'
    )

    take_down_transfer(draft_transfer, by=admin_user, reason='confirmed')

    report.refresh_from_db()
    assert report.status == AbuseReportStatus.ACTIONED
    assert report.reviewed_by_id == admin_user.id
    assert report.reviewed_at is not None


def test_take_down_transfer_does_not_action_already_resolved_reports(
    draft_transfer, uploaded_file, admin_user
):
    _active(draft_transfer)
    report = create_report(draft_transfer, reason=AbuseReportReason.OTHER)
    report.status = AbuseReportStatus.DISMISSED
    report.save()

    take_down_transfer(draft_transfer, by=admin_user, reason='confirmed')

    report.refresh_from_db()
    assert report.status == AbuseReportStatus.DISMISSED


def test_take_down_transfer_does_not_notify_by_default(
    draft_transfer, uploaded_file, admin_user, django_capture_on_commit_callbacks, monkeypatch
):
    sent = []
    monkeypatch.setattr(
        'apps.file_transfer.services.emails.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )
    _active(draft_transfer)

    with django_capture_on_commit_callbacks(execute=True):
        take_down_transfer(draft_transfer, by=admin_user, reason='confirmed')

    assert sent == []


def test_take_down_transfer_notifies_when_toggled(
    draft_transfer, uploaded_file, admin_user, django_capture_on_commit_callbacks, monkeypatch
):
    sent = []
    monkeypatch.setattr(
        'apps.file_transfer.services.emails.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )
    _active(draft_transfer)

    with django_capture_on_commit_callbacks(execute=True):
        take_down_transfer(draft_transfer, by=admin_user, reason='confirmed', notify=True)

    assert len(sent) == 1
    # Distinct wording from an ordinary expiry/deletion (issue #59 code review): "deleted and no
    # longer available" reads like the link simply ran its course, not like content was removed.
    assert 'removed' in sent[0]['subject'].lower()
    assert 'confirmed' in sent[0]['text_body']


def test_public_page_neutral_after_takedown(client, draft_transfer, uploaded_file, admin_user):
    _active(draft_transfer)
    take_down_transfer(draft_transfer, by=admin_user, reason='confirmed')

    response = client.get(reverse('t:download', args=[draft_transfer.slug]))

    assert response.status_code == 404
    content = response.content.decode()
    assert 'no longer available' in content
    assert 'confirmed' not in content


def test_dashboard_shows_taken_down_transfer_as_ended(
    client, draft_transfer, uploaded_file, admin_user
):
    _active(draft_transfer)
    take_down_transfer(draft_transfer, by=admin_user, reason='policy violation')
    client.force_login(draft_transfer.owner)

    response = client.get(reverse('file_transfer:dashboard') + '?filter=ended')

    assert response.status_code == 200
    content = response.content.decode()
    assert 'TAKEN DOWN' in content.upper()
    assert 'policy violation' in content


def test_api_get_transfer_reports_taken_down_status(
    client, draft_transfer, uploaded_file, admin_user
):
    from apps.accounts.tokens import CustomAccessToken

    _active(draft_transfer)
    take_down_transfer(draft_transfer, by=admin_user, reason='confirmed')
    headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(draft_transfer.owner)}'}

    response = client.get(f'/api/ft/transfers/{draft_transfer.id}', **headers)

    assert response.status_code == 200
    assert response.json()['status'] == 'taken_down'


# -- S3 failure during takedown (issue #59 code review): the status change and the pending
# reports' actioning must survive a failed delete, not get rolled back with it. --


def test_take_down_transfer_s3_failure_still_commits_status_and_reports(
    draft_transfer, uploaded_file, admin_user, fake_storage, monkeypatch
):
    _active(draft_transfer)
    report = create_report(
        draft_transfer, reason=AbuseReportReason.MALWARE, reporter_ip='203.0.113.1'
    )

    def _boom(*args, **kwargs):
        raise RuntimeError('S3 is down')

    monkeypatch.setattr(fake_storage, 'delete_prefix', _boom)

    with pytest.raises(RuntimeError):
        take_down_transfer(draft_transfer, by=admin_user, reason='confirmed malware')

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.TAKEN_DOWN
    assert draft_transfer.taken_down_by_id == admin_user.id
    assert draft_transfer.files_deleted_at is None  # the delete really did fail

    report.refresh_from_db()
    assert report.status == AbuseReportStatus.ACTIONED


def test_take_down_transfer_s3_failure_records_notify_choice_for_a_later_retry(
    draft_transfer, uploaded_file, admin_user, fake_storage, monkeypatch
):
    """`takedown_notify` survives the failed call so a later retry (or `jobs.expire_transfers`'s
    deferred-deletion sweep, if this transfer ends up finishing deletion there instead) can honour
    the choice staff actually made, instead of the sweep's own always-notify default."""
    _active(draft_transfer)

    def _boom(*args, **kwargs):
        raise RuntimeError('S3 is down')

    monkeypatch.setattr(fake_storage, 'delete_prefix', _boom)

    with pytest.raises(RuntimeError):
        take_down_transfer(draft_transfer, by=admin_user, reason='confirmed', notify=False)

    draft_transfer.refresh_from_db()
    assert draft_transfer.takedown_notify is False


# -- Takedown reason vs. internal note visibility (issue #59 code review) --


def test_takedown_internal_note_is_recorded_but_never_shown_on_dashboard(
    client, draft_transfer, uploaded_file, admin_user
):
    _active(draft_transfer)

    take_down_transfer(
        draft_transfer,
        by=admin_user,
        reason='confirmed malware',
        internal_note='reported by a known repeat abuser account, see ticket #1234',
    )

    draft_transfer.refresh_from_db()
    assert draft_transfer.takedown_internal_note == (
        'reported by a known repeat abuser account, see ticket #1234'
    )
    client.force_login(draft_transfer.owner)

    response = client.get(reverse('file_transfer:dashboard') + '?filter=ended')

    assert response.status_code == 200
    content = response.content.decode()
    assert 'confirmed malware' in content
    assert 'ticket #1234' not in content


def test_takedown_email_includes_reason_with_distinct_wording_from_expiry(
    draft_transfer, uploaded_file, admin_user, django_capture_on_commit_callbacks, monkeypatch
):
    sent = []
    monkeypatch.setattr(
        'apps.file_transfer.services.emails.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )
    _active(draft_transfer)

    with django_capture_on_commit_callbacks(execute=True):
        take_down_transfer(
            draft_transfer,
            by=admin_user,
            reason='confirmed malware',
            internal_note='internal only',
            notify=True,
        )

    assert len(sent) == 1
    assert 'removed' in sent[0]['subject'].lower()
    assert 'confirmed malware' in sent[0]['text_body']
    assert 'internal only' not in sent[0]['text_body']
    assert 'internal only' not in sent[0]['subject']


# -- Anonymous notify (issue #59 code review): `send_files_deleted_notification` used to bail out
# entirely when a transfer has no `owner`, silently doing nothing even with notify on. --


def test_takedown_notifies_anonymous_sender_by_sender_email(
    admin_user, fake_storage, django_capture_on_commit_callbacks, monkeypatch
):
    from ..models import Transfer

    sent = []
    monkeypatch.setattr(
        'apps.file_transfer.services.emails.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )
    transfer = Transfer.objects.create(
        owner=None,
        sender_email='anon-sender@example.com',
        status=TransferStatus.ACTIVE,
    )

    with django_capture_on_commit_callbacks(execute=True):
        take_down_transfer(transfer, by=admin_user, reason='confirmed', notify=True)

    assert len(sent) == 1
    assert sent[0]['to'] == 'anon-sender@example.com'
