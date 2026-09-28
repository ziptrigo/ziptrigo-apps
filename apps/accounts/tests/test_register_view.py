"""The register page (issue #52): session view replacing the old `fetch()`-based
`POST /api/auth/signup` flow."""

import pytest
from django.urls import reverse

from apps.accounts.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _register(client, **overrides):
    payload = {
        'name': 'New User',
        'email': 'newuser@example.com',
        'password': 'password123',
        **overrides,
    }
    return client.post(reverse('accounts:register'), payload, **HTMX)


def test_get_renders_form(client):
    response = client.get(reverse('accounts:register'))

    assert response.status_code == 200
    assert f'hx-post="{reverse("accounts:register")}"' in response.content.decode()


def test_valid_submission_creates_account_and_redirects(client):
    response = _register(client)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    user = User.objects.get(email='newuser@example.com')
    assert user.name == 'New User'
    assert user.email_confirmed is False


def test_register_without_htmx_redirects(client):
    response = client.post(
        reverse('accounts:register'),
        {'name': 'New User', 'email': 'newuser@example.com', 'password': 'password123'},
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:created')


def test_duplicate_email_returns_422(client, user):
    response = _register(client, email=user.email)

    assert response.status_code == 422
    assert 'already exists' in response.content.decode()
    assert User.objects.filter(email=user.email).count() == 1


def test_weak_password_too_short_returns_422(client):
    response = _register(client, password='pass1')

    assert response.status_code == 422
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_weak_password_no_digit_returns_422(client):
    response = _register(client, password='password')

    assert response.status_code == 422
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_blank_name_returns_422(client):
    response = _register(client, name='')

    assert response.status_code == 422
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_register_requires_csrf_token():
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    response = client.post(
        reverse('accounts:register'),
        {'name': 'New User', 'email': 'newuser@example.com', 'password': 'password123'},
    )

    assert response.status_code == 403
    assert not User.objects.filter(email='newuser@example.com').exists()


def test_signup_ip_rate_limit_is_shared_with_the_api_endpoint(client, settings):
    """`SIGNUP_IP` (issue #53) is one counter shared between this session view and
    `POST /api/auth/signup` -- exhausting it through one surface also blocks the other."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SIGNUP_IP': (1, 60 * 60)}

    client.post(
        '/api/auth/signup',
        {'name': 'A', 'email': 'a@example.com', 'password': 'password123'},
        content_type='application/json',
    )

    response = _register(client, email='b@example.com')

    assert response.status_code == 429
    assert 'Retry-After' in response
    assert not User.objects.filter(email='b@example.com').exists()
