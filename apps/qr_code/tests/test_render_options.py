"""Bounds on the image options (size, border, format, colours) the API and services accept."""

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.tokens import CustomAccessToken
from apps.qr_code import services
from apps.qr_code.models import QRCode, QRCodeType

pytestmark = [pytest.mark.django_db]

BAD_RENDER_OPTIONS = [
    {'size': 800},
    {'size': 0},
    {'border': -2},
    {'border': 21},
    {'qr_format': 'gif'},
    {'error_correction': 'X'},
    {'background_color': 'not-a-colour'},
    {'foreground_color': '#GGGGGG'},
    {'foreground_color': '"><script>'},
    {'foreground_color': 'x' * 20},
]


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def auth_headers(user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(user)}'}


def _post(client, auth_headers, path, **options):
    payload = {'name': 'Q', 'qr_type': 'text', 'qr_format': 'png', 'data': 'hello'} | options
    return client.post(path, payload, content_type='application/json', **auth_headers)


@pytest.mark.integration
@pytest.mark.parametrize('options', BAD_RENDER_OPTIONS)
@pytest.mark.parametrize('path', ['/api/qr/', '/api/qr/preview'])
def test_bad_render_options_are_a_400_and_save_nothing(client, auth_headers, path, options):
    response = _post(client, auth_headers, path, **options)

    assert response.status_code == 400
    assert not QRCode.objects.exists()


@pytest.mark.integration
@pytest.mark.parametrize('path', ['/api/qr/', '/api/qr/preview'])
@pytest.mark.parametrize('qr_format', ['png', 'svg', 'pdf'])
def test_options_at_the_limits_with_valid_colours_work(client, auth_headers, path, qr_format):
    response = _post(
        client,
        auth_headers,
        path,
        qr_format=qr_format,
        size=50,
        border=0,
        error_correction='H',
        foreground_color='#112233',
        background_color='white',
    )

    assert response.status_code in (200, 201)


@pytest.mark.integration
def test_transparent_background_is_still_allowed(client, auth_headers):
    response = _post(client, auth_headers, '/api/qr/preview', background_color='transparent')

    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.parametrize('call', ['create_qrcode', 'render_preview'])
def test_services_raise_before_anything_is_saved(user, call):
    with pytest.raises(ValidationError):
        getattr(services, call)(
            user, qr_type=QRCodeType.TEXT, content='hello', qr_format='png', size=10_000
        )

    assert not QRCode.objects.exists()
