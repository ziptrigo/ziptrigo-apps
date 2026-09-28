from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from ..models import DownloadEvent, TransferStatus
from ..services import password as password_service

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def test_download_page_unavailable_for_unknown_slug(client):
    response = client.get(reverse('t:download', args=['does-not-exist']))
    assert response.status_code == 404
    assert 'no longer available' in response.content.decode()


def test_download_page_unavailable_when_expired(client, draft_transfer):
    _active(draft_transfer, expires_at=timezone.now() - timedelta(minutes=1))
    response = client.get(reverse('t:download', args=[draft_transfer.slug]))
    assert response.status_code == 404


def test_download_page_shows_files_when_available(client, draft_transfer, uploaded_file):
    _active(draft_transfer)
    response = client.get(reverse('t:download', args=[draft_transfer.slug]))
    assert response.status_code == 200
    assert uploaded_file.name in response.content.decode()


def test_download_page_requires_password(client, draft_transfer, uploaded_file):
    _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
    response = client.get(reverse('t:download', args=[draft_transfer.slug]))
    assert response.status_code == 200
    content = response.content.decode()
    # The file list (name/size) is shown regardless of the password (spec section 3); only the
    # download link itself is gated.
    assert uploaded_file.name in content
    assert reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]) not in content
    assert 'password protected' in content.lower()


def test_unlock_wrong_password_stays_locked(client, draft_transfer, uploaded_file):
    _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
    response = client.post(
        reverse('t:unlock', args=[draft_transfer.slug]), data={'password': 'wrong'}
    )
    assert response.status_code == 422
    assert (
        reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id])
        not in response.content.decode()
    )


def test_unlock_correct_password_then_download(client, draft_transfer, uploaded_file, fake_storage):
    _active(draft_transfer, password_hash=password_service.hash_password('sekret'))

    unlock_response = client.post(
        reverse('t:unlock', args=[draft_transfer.slug]), data={'password': 'sekret'}
    )
    assert unlock_response.status_code == 302

    page_response = client.get(reverse('t:download', args=[draft_transfer.slug]))
    assert uploaded_file.name in page_response.content.decode()

    download_response = client.get(
        reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id])
    )
    assert download_response.status_code == 302
    assert uploaded_file.storage_key in download_response['Location']
    assert DownloadEvent.objects.filter(transfer=draft_transfer, file=uploaded_file).exists()


def test_download_file_without_unlocking_redirects_to_page(client, draft_transfer, uploaded_file):
    _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
    response = client.get(reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]))
    assert response.status_code == 302
    assert response['Location'] == reverse('t:download', args=[draft_transfer.slug])
    assert not DownloadEvent.objects.filter(transfer=draft_transfer).exists()


def test_download_file_records_ip_from_x_real_ip_header(
    client, draft_transfer, uploaded_file, fake_storage
):
    """`X-Real-IP` is trusted because it's nginx's own doing, not the client's -- see
    `views.download._client_ip`."""
    _active(draft_transfer)
    client.get(
        reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]),
        HTTP_X_REAL_IP='9.9.9.9',
    )
    event = DownloadEvent.objects.get(transfer=draft_transfer, file=uploaded_file)
    assert event.ip == '9.9.9.9'


def test_download_file_ignores_client_supplied_forwarded_for_header(
    client, draft_transfer, uploaded_file, fake_storage
):
    """`X-Forwarded-For` is fully client-controlled here (nginx isn't configured to sanitize it),
    so it must never be trusted for `DownloadEvent.ip` -- only `X-Real-IP` is."""
    _active(draft_transfer)
    client.get(
        reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]),
        HTTP_X_FORWARDED_FOR='9.9.9.9, 10.0.0.1',
    )
    event = DownloadEvent.objects.get(transfer=draft_transfer, file=uploaded_file)
    assert event.ip != '9.9.9.9'


def test_download_file_stores_none_ip_for_a_malformed_x_real_ip(
    client, draft_transfer, uploaded_file, fake_storage
):
    """A bogus value must not reach `GenericIPAddressField` and crash the request with a
    Postgres `DataError`."""
    _active(draft_transfer)
    response = client.get(
        reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]),
        HTTP_X_REAL_IP='not-an-ip',
    )
    assert response.status_code == 302
    event = DownloadEvent.objects.get(transfer=draft_transfer, file=uploaded_file)
    assert event.ip is None


def test_zip_status_builds_then_becomes_ready(
    client, draft_transfer, uploaded_file, fake_storage, django_capture_on_commit_callbacks
):
    """The build is enqueued from `transaction.on_commit`, which only actually runs once the
    request's transaction commits -- i.e. strictly after this first response is rendered, so it
    still shows "preparing". The task queue runs inline in tests (`ImmediateBackend`), so by the
    time a second request (the page's htmx poll) comes in, the build has already finished."""
    _active(draft_transfer)
    with django_capture_on_commit_callbacks(execute=True):
        first = client.get(reverse('t:zip-status', args=[draft_transfer.slug]))
    assert first.status_code == 200
    assert b'Preparing' in first.content

    draft_transfer.refresh_from_db()
    from ..models import ZipStatus

    assert draft_transfer.zip_status == ZipStatus.READY

    second = client.get(reverse('t:zip-status', args=[draft_transfer.slug]))
    assert b'Download all' in second.content


def test_download_zip_before_ready_redirects_to_status(client, draft_transfer, uploaded_file):
    _active(draft_transfer)
    response = client.get(reverse('t:download-zip', args=[draft_transfer.slug]))
    assert response.status_code == 302
    assert response['Location'] == reverse('t:zip-status', args=[draft_transfer.slug])


def test_download_zip_requires_password_when_locked(client, draft_transfer, uploaded_file):
    _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
    response = client.get(reverse('t:zip-status', args=[draft_transfer.slug]))
    assert response.status_code == 302
    assert response['Location'] == reverse('t:download', args=[draft_transfer.slug])


def test_download_zip_once_ready_counts_as_one_download(
    client, draft_transfer, uploaded_file, fake_storage, django_capture_on_commit_callbacks
):
    _active(draft_transfer)
    with django_capture_on_commit_callbacks(execute=True):
        client.get(reverse('t:zip-status', args=[draft_transfer.slug]))

    response = client.get(reverse('t:download-zip', args=[draft_transfer.slug]))
    assert response.status_code == 302
    event = DownloadEvent.objects.get(transfer=draft_transfer, file=None)
    assert event is not None


def test_max_downloads_enforced(client, draft_transfer, uploaded_file, fake_storage):
    _active(draft_transfer, max_downloads=1)

    first = client.get(reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]))
    assert first.status_code == 302

    second = client.get(reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]))
    assert second.status_code == 404

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED
