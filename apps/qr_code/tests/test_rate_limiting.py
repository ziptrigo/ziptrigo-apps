"""Rate limiting on qr_code endpoints (issue #53): preview and create (web + JWT API, per user),
and the public `/go/<code>` redirect (per IP, generous -- see `apps/qr_code/views/redirect.py`
for why it redirects anyway and just skips the scan-count write instead of a 429).
"""

import pytest
from django.urls import reverse

from apps.accounts.tokens import CustomAccessToken
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


@pytest.fixture
def auth_headers(user) -> dict[str, str]:
    return {'HTTP_AUTHORIZATION': f'Bearer {CustomAccessToken.for_user(user)}'}


def _create_data(**overrides) -> dict:
    return {
        'name': 'My QR',
        'qr_type': 'text',
        'qr_format': 'png',
        'url': 'hello world',
        **overrides,
    }


def _enable(settings, rule: str, limit: int = 1, window: int = 60):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, rule: (limit, window)}


class TestPreviewRateLimit:
    def test_web_429_after_limit(self, logged_in_client, settings):
        _enable(settings, 'QR_PREVIEW_USER', limit=1)

        logged_in_client.post(reverse('qr_code:preview'), _create_data(), **HTMX)
        response = logged_in_client.post(reverse('qr_code:preview'), _create_data(), **HTMX)

        assert response.status_code == 429
        assert response['HX-Retarget'] == '#qrcode-msg'
        assert 'Retry-After' in response

    def test_api_429_after_limit(self, client, auth_headers, settings):
        _enable(settings, 'QR_PREVIEW_USER', limit=1)
        payload = {'name': 'p', 'qr_type': 'text', 'qr_format': 'png', 'data': 'hello'}

        client.post('/api/qr/preview', payload, content_type='application/json', **auth_headers)
        response = client.post(
            '/api/qr/preview', payload, content_type='application/json', **auth_headers
        )

        assert response.status_code == 429
        assert response.json()['detail']
        assert 'Retry-After' in response

    def test_web_and_api_share_the_same_per_user_budget(
        self, logged_in_client, auth_headers, settings
    ):
        _enable(settings, 'QR_PREVIEW_USER', limit=1)

        logged_in_client.post(reverse('qr_code:preview'), _create_data(), **HTMX)
        response = logged_in_client.post(
            '/api/qr/preview',
            {'name': 'p', 'qr_type': 'text', 'qr_format': 'png', 'data': 'hello'},
            content_type='application/json',
            **auth_headers,
        )

        assert response.status_code == 429

    def test_different_users_have_independent_budgets(self, client, user, settings):
        from apps.accounts.tests.factories import UserFactory

        _enable(settings, 'QR_PREVIEW_USER', limit=1)
        other = UserFactory()

        client.force_login(user)
        client.post(reverse('qr_code:preview'), _create_data(), **HTMX)
        client.logout()

        client.force_login(other)
        response = client.post(reverse('qr_code:preview'), _create_data(), **HTMX)

        assert response.status_code == 200


class TestCreateRateLimit:
    def test_web_429_after_limit(self, logged_in_client, settings):
        _enable(settings, 'QR_CREATE_USER', limit=1)

        logged_in_client.post(reverse('qr_code:create-submit'), _create_data(), **HTMX)
        response = logged_in_client.post(reverse('qr_code:create-submit'), _create_data(), **HTMX)

        assert response.status_code == 429
        assert response['HX-Retarget'] == '#qrcode-msg'
        # Only the first request created a row.
        assert QRCode.objects.count() == 1

    def test_api_429_after_limit(self, client, user, auth_headers, settings):
        _enable(settings, 'QR_CREATE_USER', limit=1)
        payload = {
            'name': 'API QR',
            'qr_type': QRCodeType.TEXT.value,
            'qr_format': 'png',
            'data': 'hello',
        }

        client.post('/api/qr/', payload, content_type='application/json', **auth_headers)
        response = client.post('/api/qr/', payload, content_type='application/json', **auth_headers)

        assert response.status_code == 429
        assert QRCode.objects.filter(created_by=user).count() == 1


class TestRedirectRateLimit:
    @pytest.fixture
    def qr_code(self, user):
        return QRCode.objects.create(
            content='https://example.com',
            original_url='https://example.com',
            created_by=user,
            qr_type=QRCodeType.TEXT,
            short_code='abc123',
            image_file='test.png',
        )

    def test_redirects_and_counts_under_the_limit(self, client, qr_code, settings):
        _enable(settings, 'QR_REDIRECT_IP', limit=5)

        response = client.get(f'/go/{qr_code.short_code}/')

        assert response.status_code == 302
        assert response['Location'] == 'https://example.com'
        qr_code.refresh_from_db()
        assert qr_code.scan_count == 1

    def test_still_redirects_but_skips_the_scan_count_over_the_limit(
        self, client, qr_code, settings
    ):
        """Kinder to a real visitor scanning a physical code who happens to share an IP/NAT with
        whoever else tripped the limit: the redirect itself is stateless and must never break,
        only the scan-count write (the part that actually costs a DB write) is skipped -- see
        `apps/qr_code/views/redirect.py` and CLAUDE.md for the reasoning."""
        _enable(settings, 'QR_REDIRECT_IP', limit=1)

        client.get(f'/go/{qr_code.short_code}/')  # consumes the 1 allowed hit
        response = client.get(f'/go/{qr_code.short_code}/')

        assert response.status_code == 302
        assert response['Location'] == 'https://example.com'
        qr_code.refresh_from_db()
        assert qr_code.scan_count == 1  # not 2: the second hit's write was skipped

    def test_different_ips_have_independent_budgets(self, client, qr_code, settings):
        _enable(settings, 'QR_REDIRECT_IP', limit=1)

        client.get(f'/go/{qr_code.short_code}/', REMOTE_ADDR='10.0.0.1')
        response = client.get(f'/go/{qr_code.short_code}/', REMOTE_ADDR='10.0.0.2')

        assert response.status_code == 302
        qr_code.refresh_from_db()
        assert qr_code.scan_count == 2
