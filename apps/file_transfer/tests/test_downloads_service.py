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
    assert draft_transfer.ended_at is not None
    # Deletion is deferred: `expire_transfers` removes the objects later, once any presigned URL
    # already handed out for this download has had time to expire (see
    # `apps.file_transfer.jobs.expire_transfers` and `Transfer.files_deleted_at`).
    assert draft_transfer.files_deleted_at is None
    assert uploaded_file.storage_key in fake_storage.objects


def test_record_download_returned_url_still_resolves_once_limit_reached(
    draft_transfer, uploaded_file, fake_storage
):
    """The critical regression this guards against: with `max_downloads=1`, the download that
    reaches the limit must still get a *usable* link -- not a redirect to an object that was
    already deleted out from under it."""
    _activate(draft_transfer, max_downloads=1)

    url = downloads.record_download(draft_transfer, uploaded_file, None, storage=fake_storage)

    assert uploaded_file.storage_key in url
    assert fake_storage.object_exists(uploaded_file.storage_key)


def test_record_download_second_call_after_limit_reached_is_rejected(
    draft_transfer, uploaded_file, fake_storage
):
    """Not a true concurrency test (SQLite/the Django test transaction don't lend themselves to
    one), but it does exercise the same lock-check-record sequence `record_download` now runs
    inside `transaction.atomic()` + `select_for_update()`: once the first call has ended the
    transfer, a second attempt against the same (now unavailable) transfer must be rejected
    rather than slipping through."""
    _activate(draft_transfer, max_downloads=1)

    downloads.record_download(draft_transfer, uploaded_file, None, storage=fake_storage)

    with pytest.raises(ValidationError):
        downloads.record_download(draft_transfer, uploaded_file, None, storage=fake_storage)


def test_session_unlock_helpers(rf: RequestFactory, draft_transfer):
    request = rf.get('/')
    from django.contrib.sessions.backends.db import SessionStore

    request.session = SessionStore()
    password_hash = password.hash_password('sekret')

    assert (
        password.is_unlocked_in_session(request.session, draft_transfer.id, password_hash) is False
    )
    password.unlock_in_session(request.session, draft_transfer.id, password_hash)
    assert (
        password.is_unlocked_in_session(request.session, draft_transfer.id, password_hash) is True
    )


def test_session_unlock_is_tied_to_the_current_password_hash(rf: RequestFactory, draft_transfer):
    """Changing the password must invalidate sessions that unlocked the old one -- the session
    stores a fingerprint of the hash it was unlocked with, not a bare flag."""
    request = rf.get('/')
    from django.contrib.sessions.backends.db import SessionStore

    request.session = SessionStore()
    old_hash = password.hash_password('old-one')
    new_hash = password.hash_password('new-one')

    password.unlock_in_session(request.session, draft_transfer.id, old_hash)
    assert password.is_unlocked_in_session(request.session, draft_transfer.id, old_hash) is True
    assert password.is_unlocked_in_session(request.session, draft_transfer.id, new_hash) is False
