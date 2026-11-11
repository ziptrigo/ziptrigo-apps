"""The QR code popup on the public download page (issue #267), and the expiry line it sits next
to: a transfer that never expires says nothing about expiry."""

import base64
import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.services.qr_image import render_png

from ..models import TransferStatus
from ..services import password as password_service
from ..views import download as download_views

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

DATA_URI = re.compile(r'<img src="(data:image/png;base64,[A-Za-z0-9+/=]+)" id="qr-image"')


@pytest.fixture(autouse=True)
def _fresh_qr_cache():
    download_views._qr_data_uri_for.cache_clear()
    yield
    download_views._qr_data_uri_for.cache_clear()


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def _page(client, transfer):
    return client.get(reverse('t:download', args=[transfer.slug]))


def _png_of(body: str) -> bytes:
    match = DATA_URI.search(body)
    assert match, 'the page has no QR code image'
    return base64.b64decode(match.group(1).split(',', 1)[1])


def test_page_has_a_qr_code_button_and_dialog(client, draft_transfer, uploaded_file):
    _active(draft_transfer)
    body = _page(client, draft_transfer).content.decode()
    assert 'id="qr-code-button"' in body
    # The popup is a labelled, modal dialog with a Close button and alt text on the image.
    assert 'role="dialog" aria-modal="true" aria-labelledby="qr-modal-title"' in body
    assert 'id="qr-modal-title"' in body
    assert 'alt="QR code of the link to this download page"' in body
    assert 'id="qr-image"' in body
    assert 'x-ref="qrClose"' in body


def test_qr_code_encodes_the_absolute_download_link(
    client, draft_transfer, uploaded_file, settings
):
    settings.BASE_URL = 'https://app.example.test'
    _active(draft_transfer)
    expected = f'https://app.example.test/t/{draft_transfer.slug}/'
    assert draft_transfer.absolute_download_url == expected
    body = _page(client, draft_transfer).content.decode()
    # Same bytes as rendering the link directly: the image is a code of exactly that link.
    assert _png_of(body) == render_png(
        expected, scale=download_views.QR_SCALE, border=download_views.QR_BORDER
    )
    assert _png_of(body).startswith(b'\x89PNG\r\n\x1a\n')
    # The link is also shown as text under the code.
    assert expected in body


def test_qr_code_is_rendered_from_base_url_not_the_request_host(
    client, draft_transfer, uploaded_file, settings, monkeypatch
):
    settings.BASE_URL = 'https://app.example.test'
    settings.ALLOWED_HOSTS = ['*']
    _active(draft_transfer)
    seen = []

    def spy(content, **kwargs):
        seen.append(content)
        return render_png(content, **kwargs)

    monkeypatch.setattr(download_views, 'render_png', spy)
    response = client.get(
        reverse('t:download', args=[draft_transfer.slug]), HTTP_HOST='evil.example.net'
    )
    assert response.status_code == 200
    assert seen == [f'https://app.example.test/t/{draft_transfer.slug}/']


def test_qr_code_is_on_a_password_protected_page_and_encodes_the_page_link(
    client, draft_transfer, uploaded_file, settings
):
    settings.BASE_URL = 'https://app.example.test'
    _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
    body = _page(client, draft_transfer).content.decode()
    assert 'id="qr-code-button"' in body
    assert _png_of(body) == render_png(
        f'https://app.example.test/t/{draft_transfer.slug}/',
        scale=download_views.QR_SCALE,
        border=download_views.QR_BORDER,
    )


def test_a_refused_unlock_still_shows_the_qr_code(client, draft_transfer, uploaded_file):
    _active(draft_transfer, password_hash=password_service.hash_password('sekret'))
    response = client.post(
        reverse('t:unlock', args=[draft_transfer.slug]), data={'password': 'wrong'}
    )
    assert response.status_code == 422
    assert 'id="qr-code-button"' in response.content.decode()


def test_unavailable_pages_have_no_qr_code_and_are_unchanged(client, draft_transfer, uploaded_file):
    _active(draft_transfer, expires_at=timezone.now() - timedelta(minutes=1))
    expired = _page(client, draft_transfer)
    unknown = client.get(reverse('t:download', args=['does-not-exist']))
    assert expired.status_code == unknown.status_code == 404
    assert expired.content == unknown.content
    for response in (expired, unknown):
        body = response.content.decode()
        assert 'qr-code-button' not in body
        assert 'data:image/png' not in body
        assert 'no longer available' in body


def test_no_expiration_text_when_the_transfer_never_expires(client, draft_transfer, uploaded_file):
    _active(draft_transfer, expires_at=None)
    body = _page(client, draft_transfer).content.decode()
    assert 'No expiration' not in body
    assert 'Available until' not in body
    # The rest of the page is intact, the button included.
    assert uploaded_file.name in body
    assert 'id="qr-code-button"' in body


def test_an_expiring_transfer_still_shows_when_it_expires(client, draft_transfer, uploaded_file):
    _active(draft_transfer, expires_at=timezone.now() + timedelta(days=3))
    body = _page(client, draft_transfer).content.decode()
    assert 'Available until <time' in body
    assert 'No expiration' not in body


def test_the_rendered_code_is_cached_per_link(client, draft_transfer, uploaded_file, monkeypatch):
    _active(draft_transfer)
    calls = []
    real = download_views.render_png

    def spy(content, **kwargs):
        calls.append(content)
        return real(content, **kwargs)

    monkeypatch.setattr(download_views, 'render_png', spy)
    first = _page(client, draft_transfer).content.decode()
    second = _page(client, draft_transfer).content.decode()
    assert len(calls) == 1
    assert _png_of(first) == _png_of(second)
    assert download_views.download_qr_data_uri(draft_transfer) == (
        download_views.download_qr_data_uri(draft_transfer)
    )
    assert len(calls) == 1
