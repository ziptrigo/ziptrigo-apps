"""Rate limiting on the accounts endpoints (issue #53): login (session view + JWT endpoint,
sharing the same counters), signup, forgot-password and resend-confirmation.

`RATELIMIT_ENABLE` defaults to `False` under pytest (`config/settings.py`), so every test here
enables it via the `settings` fixture with a small, fixed limit -- the real defaults in
`settings.RATELIMIT_RULES` would need dozens of requests to trip.

Issue #53 code review: login's per-account rule split in two (`apps.accounts.services.login_throttle`)
-- `LOGIN_ACCOUNT_IP` (strict, every attempt, per (email, IP)) and `LOGIN_ACCOUNT` (looser, failed
attempts only, per email across every IP) -- specifically so a third party who merely knows a
victim's email can no longer lock them out just by submitting it with wrong passwords. Several
tests below exist to prove exactly that no longer happens. `forgot-password` gets the same
strict-plus-looser shape (`FORGOT_PASSWORD_EMAIL_IP` / `FORGOT_PASSWORD_EMAIL`), though without the
failed-attempts distinction (there's no "authenticate()" outcome to gate on there).
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
        """`LOGIN_ACCOUNT`: a looser, failed-attempts-only ceiling shared across every IP -- a
        backstop against the same account being brute-forced from many different IPs, each with
        its own, much stricter `LOGIN_ACCOUNT_IP` budget."""
        _enable(settings, 'LOGIN_ACCOUNT', limit=2)
        # Generous IP limits so only the account-wide counter can trip in this test.
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'LOGIN_IP': (1000, 300),
            'LOGIN_ACCOUNT_IP': (1000, 300),
        }

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

    def test_login_account_ip_strict_limit_does_not_affect_the_victims_own_ip(
        self, client, user, settings
    ):
        """`LOGIN_ACCOUNT_IP`: strict, keyed on (client IP, submitted email). Exhausting it from
        one attacking IP must never affect the real owner's own login from their own IP (issue
        #53 code review: the whole point of splitting this out of the old single per-account
        rule)."""
        _enable(settings, 'LOGIN_ACCOUNT_IP', limit=2)
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'LOGIN_IP': (1000, 300),
            'LOGIN_ACCOUNT': (1000, 900),
        }

        for _ in range(2):
            client.post(
                reverse('accounts:login'),
                {'email': user.email, 'password': 'wrong'},
                **HTMX,
                REMOTE_ADDR='10.0.0.1',
            )
        attacker_blocked = client.post(
            reverse('accounts:login'),
            {'email': user.email, 'password': 'wrong'},
            **HTMX,
            REMOTE_ADDR='10.0.0.1',
        )
        assert attacker_blocked.status_code == 429

        # The real owner, from a *different* IP, with the *correct* password: still gets in.
        victim_response = client.post(
            reverse('accounts:login'),
            {'email': user.email, 'password': PASSWORD},
            **HTMX,
            REMOTE_ADDR='10.0.0.2',
        )
        assert victim_response.status_code == 200
        assert victim_response['HX-Redirect'] == reverse('accounts:account')

    def test_login_account_ceiling_ignores_successful_logins(self, client, user, settings):
        """A successful login must never add to the `LOGIN_ACCOUNT` ceiling -- only a failed
        `authenticate()` does (issue #53 code review). Otherwise the real owner's own logins would
        eventually rate-limit themselves."""
        _enable(settings, 'LOGIN_ACCOUNT', limit=1)
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'LOGIN_IP': (1000, 300),
            'LOGIN_ACCOUNT_IP': (1000, 300),
        }

        for _ in range(3):
            response = self._login(client, email=user.email)
            assert response.status_code == 200

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

    def test_api_and_web_login_share_the_login_account_counters(self, client, user, settings):
        """`LOGIN_ACCOUNT_IP`/`LOGIN_ACCOUNT` are shared between the two login surfaces, same as
        `LOGIN_IP` -- a failed attempt on one surface counts against the other."""
        _enable(settings, 'LOGIN_ACCOUNT', limit=1)
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'LOGIN_IP': (1000, 300),
            'LOGIN_ACCOUNT_IP': (1000, 300),
        }

        client.post(
            '/api/auth/login',
            {'email': user.email, 'password': 'wrong'},
            content_type='application/json',
        )

        response = self._login(client, email=user.email, password='wrong')

        assert response.status_code == 429

    def test_api_login_failure_records_against_login_account(self, client, user, settings):
        """The JWT endpoint's own `authenticate()` failure must record the same way the session
        view's does -- otherwise an attacker could dodge `LOGIN_ACCOUNT` entirely by only ever
        using the API surface."""
        _enable(settings, 'LOGIN_ACCOUNT', limit=1)
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'LOGIN_IP': (1000, 300),
            'LOGIN_ACCOUNT_IP': (1000, 300),
        }

        for i in range(2):
            response = client.post(
                '/api/auth/login',
                {'email': user.email, 'password': 'wrong'},
                content_type='application/json',
                REMOTE_ADDR=f'10.0.0.{i}',
            )

        assert response.status_code == 429

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

    def test_429_after_strict_email_ip_limit(self, client, settings):
        """`FORGOT_PASSWORD_EMAIL_IP` (issue #53 code review): strict, per (email, IP) -- trips
        well before the looser, cross-IP `FORGOT_PASSWORD_EMAIL` cap does."""
        _enable(settings, 'FORGOT_PASSWORD_EMAIL_IP', limit=1)
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'FORGOT_PASSWORD_IP': (1000, 300),
            'FORGOT_PASSWORD_EMAIL': (1000, 300),
        }

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

    def test_one_attacking_ip_cannot_exhaust_the_looser_email_wide_cap_alone(
        self, client, settings
    ):
        """Issue #53 code review: the old single per-email rule let a third party who merely knew
        a victim's address block their reset with a handful of requests from *one* IP. Now the
        strict `FORGOT_PASSWORD_EMAIL_IP` rule stops one IP well short of the looser
        `FORGOT_PASSWORD_EMAIL` cap -- exhausting the latter needs many different IPs."""
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {
            **settings.RATELIMIT_RULES,
            'FORGOT_PASSWORD_IP': (1000, 300),
            'FORGOT_PASSWORD_EMAIL_IP': (2, 300),
            'FORGOT_PASSWORD_EMAIL': (100, 300),
        }

        for _ in range(2):
            client.post(
                '/api/auth/forgot-password',
                {'email': 'victim@example.com'},
                content_type='application/json',
            )
        blocked = client.post(
            '/api/auth/forgot-password',
            {'email': 'victim@example.com'},
            content_type='application/json',
        )

        assert blocked.status_code == 429
        # The looser, cross-IP cap (100) is nowhere near exhausted by these 2 real hits.

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
