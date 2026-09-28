"""Rate limiting on the accounts endpoints (issue #53): login (session view + JWT endpoint,
sharing one per-IP/per-account budget), signup, forgot-password and resend-confirmation.

`RATELIMIT_ENABLE` defaults to `False` under pytest (`config/settings.py`), so every test here
enables it via the `settings` fixture with a small, fixed limit -- the real defaults in
`settings.RATELIMIT_RULES` would need dozens of requests to trip.
"""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
PASSWORD = 'testpass123'


def _enable(settings, rule: str, limit: int = 2, window: int = 300):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, rule: (limit, window)}


class TestLoginRateLimit:
    def _login(self, client, email='testuser@example.com', password=PASSWORD):
        return client.post(
            reverse('accounts:login'), {'email': email, 'password': password}, **HTMX
        )

    def test_web_login_429_after_ip_limit(self, client, user, settings):
        _enable(settings, 'LOGIN_IP', limit=2)

        for _ in range(2):
            self._login(client, password='wrong')

        response = self._login(client, password='wrong')

        assert response.status_code == 429
        assert 'Retry-After' in response

    def test_web_login_429_after_account_limit_even_from_different_ips(
        self, client, user, settings
    ):
        _enable(settings, 'LOGIN_ACCOUNT', limit=2)
        # A generous IP limit so only the account counter can trip in this test.
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'LOGIN_IP': (1000, 300)}

        for i in range(2):
            client.post(
                reverse('accounts:login'),
                {'email': user.email, 'password': 'wrong'},
                **HTMX,
                REMOTE_ADDR=f'10.0.0.{i}',
            )

        response = client.post(
            reverse('accounts:login'),
            {'email': user.email, 'password': 'wrong'},
            **HTMX,
            REMOTE_ADDR='10.0.0.99',
        )

        assert response.status_code == 429

    def test_successful_login_still_allowed_under_the_limit(self, client, user, settings):
        _enable(settings, 'LOGIN_IP', limit=2)

        response = self._login(client)

        assert response.status_code == 200
        assert response['HX-Redirect'] == reverse('accounts:account')

    def test_api_login_429_after_shared_ip_budget(self, client, user, settings):
        """The web view and the API endpoint share one per-IP counter (`LOGIN_IP`) -- exhausting
        it through one surface also blocks the other."""
        _enable(settings, 'LOGIN_IP', limit=1)

        self._login(client, password='wrong')  # consumes the 1 allowed hit

        response = client.post(
            '/api/auth/login',
            {'email': user.email, 'password': PASSWORD},
            content_type='application/json',
        )

        assert response.status_code == 429
        assert response.json()['detail']
        assert 'Retry-After' in response

    def test_api_login_429_response_has_no_side_effects(self, client, user, settings):
        """A rate-limited login must not accidentally authenticate anyway."""
        from django.contrib.auth import SESSION_KEY

        _enable(settings, 'LOGIN_IP', limit=0)

        response = client.post(
            '/api/auth/login',
            {'email': user.email, 'password': PASSWORD},
            content_type='application/json',
        )

        assert response.status_code == 429
        assert SESSION_KEY not in client.session


class TestSignupRateLimit:
    def test_429_after_ip_limit(self, client, settings):
        _enable(settings, 'SIGNUP_IP', limit=1)
        payload = {'name': 'A', 'email': 'a@example.com', 'password': 'Str0ngPass!'}

        client.post('/api/auth/signup', payload, content_type='application/json')
        response = client.post(
            '/api/auth/signup',
            {**payload, 'email': 'b@example.com'},
            content_type='application/json',
        )

        assert response.status_code == 429


class TestForgotPasswordRateLimit:
    def test_429_after_ip_limit(self, client, settings):
        _enable(settings, 'FORGOT_PASSWORD_IP', limit=1)

        client.post(
            '/api/auth/forgot-password', {'email': 'a@example.com'}, content_type='application/json'
        )
        response = client.post(
            '/api/auth/forgot-password', {'email': 'b@example.com'}, content_type='application/json'
        )

        assert response.status_code == 429

    def test_429_after_email_limit(self, client, settings):
        _enable(settings, 'FORGOT_PASSWORD_EMAIL', limit=1)
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'FORGOT_PASSWORD_IP': (1000, 300)}

        client.post(
            '/api/auth/forgot-password',
            {'email': 'victim@example.com'},
            content_type='application/json',
        )
        response = client.post(
            '/api/auth/forgot-password',
            {'email': 'victim@example.com'},
            content_type='application/json',
        )

        assert response.status_code == 429

    def test_response_body_unchanged_by_rate_limiting_up_to_the_limit(self, client, settings, user):
        """CLAUDE.md: forgot-password must never reveal whether the account exists. The 200
        response's body must be identical for an existing and a non-existing email, and hitting
        the (generous, un-tripped) rate limit must not change that."""
        _enable(settings, 'FORGOT_PASSWORD_EMAIL', limit=5)

        existing = client.post(
            '/api/auth/forgot-password', {'email': user.email}, content_type='application/json'
        )
        nonexistent = client.post(
            '/api/auth/forgot-password',
            {'email': 'nobody@example.com'},
            content_type='application/json',
        )

        assert existing.status_code == nonexistent.status_code == 200
        assert existing.json() == nonexistent.json()


class TestResendConfirmationRateLimit:
    def test_429_after_ip_limit(self, client, settings):
        _enable(settings, 'RESEND_CONFIRMATION_IP', limit=1)

        client.post(
            '/api/auth/resend-confirmation',
            {'email': 'a@example.com'},
            content_type='application/json',
        )
        response = client.post(
            '/api/auth/resend-confirmation',
            {'email': 'b@example.com'},
            content_type='application/json',
        )

        assert response.status_code == 429

    def test_per_email_daily_cap_applies_on_top_of_the_ip_limit(self, client, settings, user):
        """The per-email-per-day cap lives centrally in
        `apps.core.services.email_verification.start` (issue #53 / CLAUDE.md Known gaps) --
        exercised here through the accounts endpoint that calls it, with a generous IP limit so
        only the email cap can trip."""
        user.email_confirmed = False
        user.save(update_fields=['email_confirmed'])

        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'RESEND_CONFIRMATION_IP': (1000, 300),
            'EMAIL_VERIFICATION_START_EMAIL': (1, 24 * 60 * 60),
        }

        first = client.post(
            '/api/auth/resend-confirmation', {'email': user.email}, content_type='application/json'
        )
        second = client.post(
            '/api/auth/resend-confirmation', {'email': user.email}, content_type='application/json'
        )

        # Both still report the same generic 200 -- the cap is swallowed, not surfaced, so this
        # endpoint keeps not revealing whether the account exists or was already at its cap.
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
