"""The QR code JWT API (`/api/qr/`), as used by external clients."""

import uuid

import pytest

from apps.accounts.models import User
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
    assert data['image_url'] == f'/api/qr/{data["id"]}/image'


def test_preview_is_not_shadowed_by_detail_route(client, auth_headers):
    response = client.post(
        '/api/qr/preview',
        {'name': 'p', 'qr_type': 'text', 'qr_format': 'png', 'data': 'hello'},
        content_type='application/json',
        **auth_headers,
    )

    assert response.status_code == 200
    assert response.json()['image_url'].startswith('data:image/png;base64,')
    assert not QRCode.objects.exists()


def test_create_text_type_has_no_original_url_or_short_link(client, user, auth_headers):
    response = client.post(
        '/api/qr/',
        {
            'name': 'Text',
            'qr_type': 'text',
            'qr_format': 'png',
            'url': 'just some text',
            'use_url_shortening': True,
        },
        content_type='application/json',
        **auth_headers,
    )

    assert response.status_code == 201
    qrcode = QRCode.objects.get(id=response.json()['id'])
    assert qrcode.original_url is None
    assert not qrcode.use_url_shortening
    assert qrcode.content == 'just some text'


@pytest.mark.parametrize('url', ['not a url', 'javascript:alert(1)'])
def test_create_rejects_invalid_url(client, auth_headers, url):
    response = client.post(
        '/api/qr/',
        {'name': 'Bad', 'qr_type': 'url', 'qr_format': 'png', 'url': url},
        content_type='application/json',
        **auth_headers,
    )

    assert response.status_code == 400
    assert 'valid URL' in response.json()['detail']
    assert not QRCode.objects.exists()


def test_create_rejects_empty_content(client, auth_headers):
    response = client.post(
        '/api/qr/',
        {'name': 'Empty', 'qr_type': 'text', 'qr_format': 'png'},
        content_type='application/json',
        **auth_headers,
    )

    assert response.status_code == 400


def test_requires_token(client):
    assert client.get('/api/qr/').status_code == 401


def _body_kwargs(method, auth_headers):
    kwargs = {'content_type': 'application/json', **auth_headers}
    if method in ('put', 'patch'):
        kwargs['data'] = {'name': 'new'}
    return kwargs


@pytest.mark.parametrize('method', ['get', 'put', 'patch', 'delete'])
def test_unknown_id_is_404(client, auth_headers, method):
    response = getattr(client, method)(
        f'/api/qr/{uuid.uuid4()}', **_body_kwargs(method, auth_headers)
    )

    assert response.status_code == 404
    assert response.json() == {'detail': 'QR code not found.'}


@pytest.mark.parametrize('method', ['get', 'put', 'patch', 'delete'])
def test_another_users_qr_code_is_404(client, auth_headers, method):
    other = User.objects.create_user(email='other@example.com', password='testpass123')
    foreign = QRCode.objects.create(
        content='x', created_by=other, qr_type='text', qr_format='png', image_file='x.png'
    )
    original_name = foreign.name

    response = getattr(client, method)(
        f'/api/qr/{foreign.id}', **_body_kwargs(method, auth_headers)
    )

    assert response.status_code == 404
    foreign.refresh_from_db()
    assert foreign.deleted_at is None
    assert foreign.name == original_name


def test_deleting_twice_is_404_the_second_time(client, qr_code, auth_headers):
    first = client.delete(f'/api/qr/{qr_code.id}', **auth_headers)
    second = client.delete(f'/api/qr/{qr_code.id}', **auth_headers)

    assert first.status_code == 204
    assert second.status_code == 404
    assert client.get(f'/api/qr/{qr_code.id}', **auth_headers).status_code == 404


def test_image_endpoint_renders_a_png_without_a_media_file(client, user, auth_headers, qr_code):
    # `qr_code.image_file` names a file that doesn't exist, as after a redeploy.
    response = client.get(f'/api/qr/{qr_code.id}/image', **auth_headers)

    assert response.status_code == 200
    assert response['Content-Type'] == 'image/png'
    assert response.content.startswith(b'\x89PNG')


def test_image_endpoint_hides_other_users_and_deleted_codes(client, auth_headers, qr_code):
    other = User.objects.create_user(email='other@example.com', password='x')
    other_headers = {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(other)}'}
    assert client.get(f'/api/qr/{qr_code.id}/image', **other_headers).status_code == 404

    qr_code.soft_delete()
    assert client.get(f'/api/qr/{qr_code.id}/image', **auth_headers).status_code == 404
