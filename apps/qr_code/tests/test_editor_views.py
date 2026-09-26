"""The QR code editor's session-authenticated form views (not the JWT API)."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.qr_code.models import QRCode, QRCodeType

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def logged_in_client(client, user):
    client.force_login(user)
    return client


def _create_data(**overrides) -> dict:
    return {
        'name': 'My QR',
        'qr_type': 'text',
        'qr_format': 'png',
        'url': 'hello world',
        **overrides,
    }


class TestCreate:
    def test_creates_qrcode_and_redirects_to_dashboard(self, logged_in_client, user, tmp_path):
        response = logged_in_client.post(reverse('qrcode-create-submit'), _create_data(), **HTMX)

        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('dashboard')
        qrcode = QRCode.objects.get(created_by=user)
        assert qrcode.name == 'My QR'
        assert qrcode.qr_type == QRCodeType.TEXT
        assert qrcode.content == 'hello world'
        assert qrcode.original_url is None
        assert not qrcode.use_url_shortening
        assert (tmp_path / qrcode.image_file).is_file()

    def test_without_htmx_redirects(self, logged_in_client):
        response = logged_in_client.post(reverse('qrcode-create-submit'), _create_data())

        assert response.status_code == 302
        assert response['Location'] == reverse('dashboard')

    def test_url_with_shortening_uses_requested_short_code(self, logged_in_client, user):
        data = _create_data(
            qr_type='url',
            url='https://example.com/page',
            use_url_shortening='true',
            short_code='Abcd1234',
        )

        logged_in_client.post(reverse('qrcode-create-submit'), data, **HTMX)

        qrcode = QRCode.objects.get(created_by=user)
        assert qrcode.short_code == 'Abcd1234'
        assert qrcode.original_url == 'https://example.com/page'
        assert qrcode.content == qrcode.get_redirect_url()

    def test_taken_short_code_is_replaced(self, logged_in_client, user, qr_code_with_shortening):
        taken = qr_code_with_shortening.short_code
        data = _create_data(
            qr_type='url', url='https://example.com', use_url_shortening='true', short_code=taken
        )

        logged_in_client.post(reverse('qrcode-create-submit'), data, **HTMX)

        qrcode = QRCode.objects.exclude(pk=qr_code_with_shortening.pk).get(created_by=user)
        assert qrcode.short_code
        assert qrcode.short_code != taken

    def test_text_type_ignores_shortening(self, logged_in_client, user):
        data = _create_data(use_url_shortening='true', short_code='Abcd1234')

        logged_in_client.post(reverse('qrcode-create-submit'), data, **HTMX)

        qrcode = QRCode.objects.get(created_by=user)
        assert not qrcode.use_url_shortening
        assert qrcode.short_code is None

    def test_invalid_url_returns_errors(self, logged_in_client):
        data = _create_data(qr_type='url', url='not a url')

        response = logged_in_client.post(reverse('qrcode-create-submit'), data, **HTMX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#qrcode-msg'
        assert 'valid URL' in response.content.decode()
        assert not QRCode.objects.exists()

    def test_missing_name_returns_errors(self, logged_in_client):
        response = logged_in_client.post(
            reverse('qrcode-create-submit'), _create_data(name=''), **HTMX
        )

        assert response.status_code == 422
        assert 'Name' in response.content.decode()

    def test_requires_login(self, client):
        response = client.post(reverse('qrcode-create-submit'), _create_data())

        assert response.status_code == 302
        assert response['Location'].startswith(reverse('login-page'))
        assert not QRCode.objects.exists()

    def test_rejects_get(self, logged_in_client):
        assert logged_in_client.get(reverse('qrcode-create-submit')).status_code == 405


class TestEdit:
    def test_renames(self, logged_in_client, qr_code):
        response = logged_in_client.post(
            reverse('qrcode-edit-submit', args=[qr_code.id]), {'name': 'Renamed'}, **HTMX
        )

        assert response['HX-Redirect'] == reverse('dashboard')
        qr_code.refresh_from_db()
        assert qr_code.name == 'Renamed'

    def test_missing_name_returns_errors(self, logged_in_client, qr_code):
        response = logged_in_client.post(
            reverse('qrcode-edit-submit', args=[qr_code.id]), {'name': ''}, **HTMX
        )

        assert response.status_code == 422

    def test_other_users_qrcode_is_not_found(self, client, qr_code):
        other = User.objects.create_user(email='other@example.com', password='password123')
        client.force_login(other)

        response = client.post(
            reverse('qrcode-edit-submit', args=[qr_code.id]), {'name': 'Hijacked'}, **HTMX
        )

        assert response.status_code == 404
        qr_code.refresh_from_db()
        assert qr_code.name != 'Hijacked'


class TestPreview:
    def test_returns_image_partial_without_saving(self, logged_in_client, tmp_path):
        response = logged_in_client.post(reverse('qrcode-preview'), _create_data(), **HTMX)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="qrcode-preview-img"' in content
        assert '/media/qrcodes/' in content
        assert '<html' not in content
        assert not QRCode.objects.exists()
        assert list((tmp_path / 'qrcodes').iterdir())

    def test_invalid_returns_errors(self, logged_in_client):
        response = logged_in_client.post(reverse('qrcode-preview'), _create_data(url=''), **HTMX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#qrcode-msg'


class TestDelete:
    def test_soft_deletes(self, logged_in_client, qr_code):
        response = logged_in_client.post(reverse('qrcode-delete', args=[qr_code.id]))

        assert response.status_code == 204
        qr_code.refresh_from_db()
        assert qr_code.deleted_at is not None

    def test_other_users_qrcode_is_not_found(self, client, qr_code):
        other = User.objects.create_user(email='other@example.com', password='password123')
        client.force_login(other)

        response = client.post(reverse('qrcode-delete', args=[qr_code.id]))

        assert response.status_code == 404
        qr_code.refresh_from_db()
        assert qr_code.deleted_at is None
