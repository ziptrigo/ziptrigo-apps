"""The session login/logout views (the web UI never uses JWTs)."""

import pytest
from django.contrib.auth import SESSION_KEY
from django.urls import reverse

from apps.accounts.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
PASSWORD = 'testpass123'


def _login(client, email='testuser@example.com', password=PASSWORD, **extra):
    return client.post(
        reverse('accounts:login'), {'email': email, 'password': password, **extra}, **HTMX
    )


def test_get_renders_form(client):
    response = client.get(reverse('accounts:login'))

    assert response.status_code == 200
    assert f'hx-post="{reverse("accounts:login")}"' in response.content.decode()


def test_login_starts_session_and_redirects_to_account(client, user):
    response = _login(client)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:account')
    assert client.session[SESSION_KEY] == str(user.pk)


def test_login_without_htmx_redirects(client, user):
    response = client.post(reverse('accounts:login'), {'email': user.email, 'password': PASSWORD})

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')


def test_login_follows_safe_next(client, user):
    response = _login(client, next='/qr/')

    assert response['HX-Redirect'] == '/qr/'


def test_login_ignores_offsite_next(client, user):
    response = _login(client, next='https://evil.example/steal')

    assert response['HX-Redirect'] == reverse('accounts:account')


@pytest.mark.parametrize(
    ('email', 'password', 'message'),
    [
        ('testuser@example.com', 'wrong', 'Invalid credentials'),
        ('nobody@example.com', PASSWORD, 'Invalid credentials'),
    ],
)
def test_bad_credentials(client, user, email, password, message):
    response = _login(client, email=email, password=password)

    assert response.status_code == 422
    assert message in response.content.decode()
    assert SESSION_KEY not in client.session


def test_inactive_user_is_rejected(client, user):
    user.status = User.STATUS_INACTIVE
    user.save(update_fields=['status'])

    response = _login(client)

    assert response.status_code == 422
    assert 'User not active' in response.content.decode()


def test_unconfirmed_email_is_rejected(client, user):
    user.email_confirmed = False
    user.save(update_fields=['email_confirmed'])

    response = _login(client)

    assert response.status_code == 422
    assert 'confirm your email' in response.content.decode()
    assert SESSION_KEY not in client.session


def test_login_requires_csrf_token(user):
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    response = client.post(reverse('accounts:login'), {'email': user.email, 'password': PASSWORD})

    assert response.status_code == 403


def test_api_login_does_not_start_a_session(client, user):
    response = client.post(
        '/api/auth/login',
        {'email': user.email, 'password': PASSWORD},
        content_type='application/json',
    )

    assert response.status_code == 200
    assert 'access_token' in response.json()
    assert SESSION_KEY not in client.session


def test_logout_requires_post(client, user):
    client.force_login(user)

    assert client.get(reverse('accounts:logout')).status_code == 405
    assert SESSION_KEY in client.session


def test_logout_ends_session(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:logout'))

    assert response.status_code == 302
    assert response['Location'] == reverse('core:home')
    assert SESSION_KEY not in client.session
