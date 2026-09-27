from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils import timezone

from ..models import DownloadEvent, TransferStatus
from ..services import downloads, password

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _activate(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def test_is_available_false_for_non_active_status(draft_transfer):
    assert downloads.is_available(draft_transfer) is False


def test_is_available_false_when_expired(draft_transfer):
    _activate(draft_transfer, expires_at=timezone.now() - timedelta(minutes=1))
    assert downloads.is_available(draft_transfer) is False


def test_is_available_true_when_not_yet_expired(draft_transfer):
    _activate(draft_transfer, expires_at=timezone.now() + timedelta(days=1))
    assert downloads.is_available(draft_transfer) is True


def test_is_available_false_when_max_downloads_reached(draft_transfer, uploaded_file):
    _activate(draft_transfer, max_downloads=1)
    DownloadEvent.objects.create(transfer=draft_transfer, file=uploaded_file)
    assert downloads.is_available(draft_transfer) is False


def test_check_password_true_when_no_password_set(draft_transfer):
    assert downloads.check_password(draft_transfer, 'anything') is True


def test_check_password_verifies_hash(draft_transfer):
    draft_transfer.password_hash = password.hash_password('correct-horse')
    assert downloads.check_password(draft_transfer, 'wrong') is False
    assert downloads.check_password(draft_transfer, 'correct-horse') is True


def test_record_download_creates_event_and_returns_url(draft_transfer, uploaded_file, fake_storage):
    _activate(draft_transfer)
    url = downloads.record_download(draft_transfer, uploaded_file, '1.2.3.4', storage=fake_storage)

    event = DownloadEvent.objects.get(transfer=draft_transfer)
    assert event.file_id == uploaded_file.id
    assert event.ip == '1.2.3.4'
    assert uploaded_file.storage_key in url


def test_record_download_rejects_unavailable_transfer(draft_transfer, uploaded_file, fake_storage):
    # still a draft: not available
    with pytest.raises(ValidationError):
        downloads.record_download(draft_transfer, uploaded_file, None, storage=fake_storage)


def test_record_download_rejects_file_from_other_transfer(
    draft_transfer, uploaded_file, fake_storage, funded_user
):
    from ..models import Transfer

    _activate(draft_transfer)
    other_transfer = Transfer.objects.create(owner=funded_user, status=TransferStatus.ACTIVE)

    with pytest.raises(ValidationError):
        downloads.record_download(other_transfer, uploaded_file, None, storage=fake_storage)


def test_record_download_ends_transfer_once_limit_reached(
    draft_transfer, uploaded_file, fake_storage
):
    _activate(draft_transfer, max_downloads=1)

    downloads.record_download(draft_transfer, uploaded_file, None, storage=fake_storage)

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED
    # Files were deleted as part of ending the transfer.
    assert uploaded_file.storage_key not in fake_storage.objects


def test_session_unlock_helpers(rf: RequestFactory, draft_transfer):
    request = rf.get('/')
    from django.contrib.sessions.backends.db import SessionStore

    request.session = SessionStore()

    assert password.is_unlocked_in_session(request.session, draft_transfer.id) is False
    password.unlock_in_session(request.session, draft_transfer.id)
    assert password.is_unlocked_in_session(request.session, draft_transfer.id) is True
