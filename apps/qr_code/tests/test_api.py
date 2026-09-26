"""The QR code JWT API (`/api/qr/`), as used by external clients."""

import pytest

from apps.accounts.tokens import CustomAccessToken
from apps.qr_code.models import QRCode

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def auth_headers(user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(user)}'}


def test_create_with_shortening_encodes_redirect_url(client, user, auth_headers):
    response = client.post(
        '/api/qr/',
        {
            'name': 'API QR',
            'qr_type': 'url',
            'qr_format': 'png',
            'url': 'https://example.com',
            'use_url_shortening': True,
        },
        content_type='application/json',
        **auth_headers,
    )

    assert response.status_code == 201
    data = response.json()
    qrcode = QRCode.objects.get(id=data['id'], created_by=user)
    assert qrcode.original_url == 'https://example.com'
    assert qrcode.content == qrcode.get_redirect_url() == data['redirect_url']
    assert data['image_url'].endswith('.png')


def test_preview_is_not_shadowed_by_detail_route(client, auth_headers):
    response = client.post(
        '/api/qr/preview',
        {'name': 'p', 'qr_type': 'text', 'qr_format': 'png', 'data': 'hello'},
        content_type='application/json',
        **auth_headers,
    )

    assert response.status_code == 200
    assert response.json()['image_url'].startswith('/media/qrcodes/')
    assert not QRCode.objects.exists()


def test_requires_token(client):
    assert client.get('/api/qr/').status_code == 401
