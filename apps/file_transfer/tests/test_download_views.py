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


def test_download_file_records_ip_from_forwarded_header(
    client, draft_transfer, uploaded_file, fake_storage
):
    _active(draft_transfer)
    client.get(
        reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]),
        HTTP_X_FORWARDED_FOR='9.9.9.9, 10.0.0.1',
    )
    event = DownloadEvent.objects.get(transfer=draft_transfer, file=uploaded_file)
    assert event.ip == '9.9.9.9'


def test_max_downloads_enforced(client, draft_transfer, uploaded_file, fake_storage):
    _active(draft_transfer, max_downloads=1)

    first = client.get(reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]))
    assert first.status_code == 302

    second = client.get(reverse('t:download-file', args=[draft_transfer.slug, uploaded_file.id]))
    assert second.status_code == 404

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.EXPIRED
