"""Resend-confirmation on the expired-confirmation-link page (issue #52): session view for
`POST /api/auth/resend-confirmation`, which previously had no page wired to it at all."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
SENT_MESSAGE = 'If the account exists and is not yet confirmed, a confirmation email will be sent.'


def _resend(client, email, **extra):
    return client.post(reverse('accounts:resend-confirmation'), {'email': email}, **HTMX, **extra)


def test_get_is_not_allowed(client):
    response = client.get(reverse('accounts:resend-confirmation'))

    assert response.status_code == 405


def test_expired_confirmation_link_page_includes_the_resend_form(client):
    response = client.get(reverse('accounts:confirm-email', args=['not-a-real-token']))

    assert response.status_code == 200
    assert f'hx-post="{reverse("accounts:resend-confirmation")}"' in response.content.decode()


def test_unconfirmed_account_returns_generic_success(client, user):
    user.email_confirmed = False
    user.save(update_fields=['email_confirmed'])

    response = _resend(client, user.email)

    assert response.status_code == 200
    assert SENT_MESSAGE in response.content.decode()


def test_confirmed_account_returns_the_same_generic_success(client, user):
    """CLAUDE.md: never reveal whether the address is registered or already confirmed."""
    response = _resend(client, user.email)

    assert response.status_code == 200
    assert SENT_MESSAGE in response.content.decode()


def test_unknown_email_returns_the_same_generic_success(client):
    response = _resend(client, 'nobody@example.com')

    assert response.status_code == 200
    assert SENT_MESSAGE in response.content.decode()


def test_invalid_email_format_returns_422(client):
    response = _resend(client, 'not-an-email')

    assert response.status_code == 422


def test_resend_confirmation_requires_csrf_token(user):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    response = client.post(reverse('accounts:resend-confirmation'), {'email': user.email})

    assert response.status_code == 403


def test_429_after_ip_limit(client, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'RESEND_CONFIRMATION_IP': (1, 300)}

    _resend(client, 'a@example.com')
    response = _resend(client, 'b@example.com')

    assert response.status_code == 429
    assert 'Retry-After' in response


def test_ip_rate_limit_shared_with_api_endpoint(client, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'RESEND_CONFIRMATION_IP': (1, 300)}

    _resend(client, 'a@example.com')
    response = client.post(
        '/api/auth/resend-confirmation',
        {'email': 'b@example.com'},
        content_type='application/json',
    )

    assert response.status_code == 429
