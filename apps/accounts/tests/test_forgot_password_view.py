"""The forgot-password page (issue #52): session view replacing the old `fetch()`-based
`POST /api/auth/forgot-password` flow. CLAUDE.md: must never reveal whether the account exists."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
SENT_MESSAGE = 'If the account exists, an email will be sent with a password reset link.'


def _forgot_password(client, email, **extra):
    return client.post(reverse('accounts:forgot-password'), {'email': email}, **HTMX, **extra)


def test_get_renders_form(client):
    response = client.get(reverse('accounts:forgot-password'))

    assert response.status_code == 200
    assert f'hx-post="{reverse("accounts:forgot-password")}"' in response.content.decode()


def test_existing_email_returns_generic_success(client, user):
    response = _forgot_password(client, user.email)

    assert response.status_code == 200
    assert SENT_MESSAGE in response.content.decode()


def test_nonexistent_email_returns_the_same_generic_success(client, user):
    """CLAUDE.md: the response must be indistinguishable whether or not the account exists."""
    existing = _forgot_password(client, user.email)
    nonexistent = _forgot_password(client, 'nobody@example.com')

    assert existing.status_code == nonexistent.status_code == 200
    assert SENT_MESSAGE in existing.content.decode()
    assert SENT_MESSAGE in nonexistent.content.decode()


def test_invalid_email_format_returns_422(client):
    response = _forgot_password(client, 'not-an-email')

    assert response.status_code == 422


def test_forgot_password_without_htmx_renders_full_page(client, user):
    response = client.post(reverse('accounts:forgot-password'), {'email': user.email})

    assert response.status_code == 200
    assert SENT_MESSAGE in response.content.decode()
    assert '<html' in response.content.decode().lower()


def test_forgot_password_requires_csrf_token(user):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    response = client.post(reverse('accounts:forgot-password'), {'email': user.email})

    assert response.status_code == 403


class TestRateLimiting:
    """Mirrors `apps.accounts.tests.test_rate_limiting.TestForgotPasswordRateLimit`, but for this
    session view -- same three rules (issue #53), shared counters with the API endpoint."""

    def _enable(self, settings, rule: str, limit: int = 2, window: int = 300):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, rule: (limit, window)}

    def test_429_after_ip_limit(self, client, settings):
        self._enable(settings, 'FORGOT_PASSWORD_IP', limit=1)

        _forgot_password(client, 'a@example.com')
        response = _forgot_password(client, 'b@example.com')

        assert response.status_code == 429
        assert 'Retry-After' in response

    def test_429_after_email_limit(self, client, settings):
        self._enable(settings, 'FORGOT_PASSWORD_EMAIL', limit=1)
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FORGOT_PASSWORD_IP': (1000, 300)}

        _forgot_password(client, 'victim@example.com')
        response = _forgot_password(client, 'victim@example.com')

        assert response.status_code == 429

    def test_ip_rate_limit_shared_with_api_endpoint(self, client, settings):
        self._enable(settings, 'FORGOT_PASSWORD_IP', limit=1)

        _forgot_password(client, 'a@example.com')
        response = client.post(
            '/api/auth/forgot-password',
            {'email': 'b@example.com'},
            content_type='application/json',
        )

        assert response.status_code == 429

    def test_rate_limited_response_still_does_not_leak_existence(self, client, settings, user):
        """Even the 429 path must not distinguish an existing account from a non-existent one --
        it's generic, and reached identically either way."""
        self._enable(settings, 'FORGOT_PASSWORD_EMAIL_IP', limit=0)

        existing = _forgot_password(client, user.email, REMOTE_ADDR='10.0.0.1')
        nonexistent = _forgot_password(client, 'nobody@example.com', REMOTE_ADDR='10.0.0.2')

        assert existing.status_code == nonexistent.status_code == 429
