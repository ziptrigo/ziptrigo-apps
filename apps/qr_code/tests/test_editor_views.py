"""The QR code editor's session-authenticated form views (not the JWT API)."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.qr_code.models import QRCode, QRCodeType
from apps.qr_code.views.editor import SHORT_CODE_SESSION_KEY

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
        response = logged_in_client.post(reverse('qr_code:create-submit'), _create_data(), **HTMX)

        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('qr_code:dashboard')
        qrcode = QRCode.objects.get(created_by=user)
        assert qrcode.name == 'My QR'
        assert qrcode.qr_type == QRCodeType.TEXT
        assert qrcode.content == 'hello world'
        assert qrcode.original_url is None
        assert not qrcode.use_url_shortening
        assert (tmp_path / qrcode.image_file).is_file()

    def test_without_htmx_redirects(self, logged_in_client):
        response = logged_in_client.post(reverse('qr_code:create-submit'), _create_data())

        assert response.status_code == 302
        assert response['Location'] == reverse('qr_code:dashboard')

    def test_url_with_shortening_uses_the_issued_short_code(self, logged_in_client, user):
        issued = logged_in_client.get(reverse('qr_code:short-code')).content.decode()
        short_code = logged_in_client.session[SHORT_CODE_SESSION_KEY]
        assert f'/go/{short_code}' in issued

        data = _create_data(
            qr_type='url', url='https://example.com/page', use_url_shortening='true'
        )
        logged_in_client.post(reverse('qr_code:create-submit'), data, **HTMX)

        qrcode = QRCode.objects.get(created_by=user)
        assert qrcode.short_code == short_code
        assert qrcode.original_url == 'https://example.com/page'
        assert qrcode.content == qrcode.get_redirect_url()
        assert SHORT_CODE_SESSION_KEY not in logged_in_client.session

    def test_client_supplied_short_code_is_ignored(self, logged_in_client, user):
        data = _create_data(
            qr_type='url',
            url='https://example.com',
            use_url_shortening='true',
            short_code='Vanity123',
        )

        logged_in_client.post(reverse('qr_code:create-submit'), data, **HTMX)

        qrcode = QRCode.objects.get(created_by=user)
        assert qrcode.short_code
        assert qrcode.short_code != 'Vanity123'

    def test_issued_short_code_taken_meanwhile_is_replaced(
        self, logged_in_client, user, qr_code_with_shortening
    ):
        session = logged_in_client.session
        session[SHORT_CODE_SESSION_KEY] = qr_code_with_shortening.short_code
        session.save()
        data = _create_data(qr_type='url', url='https://example.com', use_url_shortening='true')

        logged_in_client.post(reverse('qr_code:create-submit'), data, **HTMX)

        qrcode = QRCode.objects.exclude(pk=qr_code_with_shortening.pk).get(created_by=user)
        assert qrcode.short_code
        assert qrcode.short_code != qr_code_with_shortening.short_code

    def test_text_type_ignores_shortening(self, logged_in_client, user):
        logged_in_client.get(reverse('qr_code:short-code'))
        data = _create_data(use_url_shortening='true')

        logged_in_client.post(reverse('qr_code:create-submit'), data, **HTMX)

        qrcode = QRCode.objects.get(created_by=user)
        assert not qrcode.use_url_shortening
        assert qrcode.short_code is None
        assert qrcode.original_url is None

    def test_invalid_url_returns_errors(self, logged_in_client):
        data = _create_data(qr_type='url', url='not a url')

        response = logged_in_client.post(reverse('qr_code:create-submit'), data, **HTMX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#qrcode-msg'
        assert 'valid URL' in response.content.decode()
        assert not QRCode.objects.exists()

    def test_missing_name_returns_errors(self, logged_in_client):
        response = logged_in_client.post(
            reverse('qr_code:create-submit'), _create_data(name=''), **HTMX
        )

        assert response.status_code == 422
        assert 'Name' in response.content.decode()

    def test_requires_login(self, client):
        response = client.post(reverse('qr_code:create-submit'), _create_data())

        assert response.status_code == 302
        assert response['Location'].startswith(reverse('accounts:login'))
        assert not QRCode.objects.exists()

    def test_rejects_get(self, logged_in_client):
        assert logged_in_client.get(reverse('qr_code:create-submit')).status_code == 405


class TestEdit:
    def test_renames(self, logged_in_client, qr_code):
        response = logged_in_client.post(
            reverse('qr_code:edit-submit', args=[qr_code.id]), {'name': 'Renamed'}, **HTMX
        )

        assert response['HX-Redirect'] == reverse('qr_code:dashboard')
        qr_code.refresh_from_db()
        assert qr_code.name == 'Renamed'

    def test_missing_name_returns_errors(self, logged_in_client, qr_code):
        response = logged_in_client.post(
            reverse('qr_code:edit-submit', args=[qr_code.id]), {'name': ''}, **HTMX
        )

        assert response.status_code == 422

    def test_other_users_qrcode_is_not_found(self, client, qr_code):
        other = User.objects.create_user(email='other@example.com', password='password123')
        client.force_login(other)

        response = client.post(
            reverse('qr_code:edit-submit', args=[qr_code.id]), {'name': 'Hijacked'}, **HTMX
        )

        assert response.status_code == 404
        qr_code.refresh_from_db()
        assert qr_code.name != 'Hijacked'


class TestShortCode:
    def test_issues_a_stable_code_per_session(self, logged_in_client):
        first = logged_in_client.get(reverse('qr_code:short-code'))
        second = logged_in_client.get(reverse('qr_code:short-code'))

        assert first.status_code == 200
        assert first.content == second.content
        assert 'id="short-url-display"' in first.content.decode()

    def test_requires_login(self, client):
        response = client.get(reverse('qr_code:short-code'))

        assert response.status_code == 302


class TestPreview:
    def test_returns_inline_image_without_saving_or_writing_files(self, logged_in_client, tmp_path):
        response = logged_in_client.post(reverse('qr_code:preview'), _create_data(), **HTMX)

        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="qrcode-preview-img"' in content
        assert 'src="data:image/png;base64,' in content
        assert '<html' not in content
        assert not QRCode.objects.exists()
        assert not list(tmp_path.iterdir())

    def test_tracked_preview_encodes_the_issued_short_link(
        self, logged_in_client, monkeypatch, settings
    ):
        settings.BASE_URL = 'https://zt.example'
        logged_in_client.get(reverse('qr_code:short-code'))
        short_code = logged_in_client.session[SHORT_CODE_SESSION_KEY]
        encoded = []
        monkeypatch.setattr(
            'apps.qr_code.services.management.QRCodeGenerator.render_png_data_uri',
            lambda qrcode: encoded.append(qrcode.content) or 'data:image/png;base64,',
        )
        data = _create_data(qr_type='url', url='https://example.com', use_url_shortening='true')

        logged_in_client.post(reverse('qr_code:preview'), data, **HTMX)

        assert encoded == [f'https://zt.example/go/{short_code}']

    def test_invalid_returns_errors(self, logged_in_client):
        response = logged_in_client.post(reverse('qr_code:preview'), _create_data(url=''), **HTMX)

        assert response.status_code == 422
        assert response['HX-Retarget'] == '#qrcode-msg'


class TestDelete:
    def test_soft_deletes(self, logged_in_client, qr_code):
        response = logged_in_client.post(reverse('qr_code:delete', args=[qr_code.id]))

        assert response.status_code == 204
        qr_code.refresh_from_db()
        assert qr_code.deleted_at is not None

    def test_other_users_qrcode_is_not_found(self, client, qr_code):
        other = User.objects.create_user(email='other@example.com', password='password123')
        client.force_login(other)

        response = client.post(reverse('qr_code:delete', args=[qr_code.id]))

        assert response.status_code == 404
        qr_code.refresh_from_db()
        assert qr_code.deleted_at is None
