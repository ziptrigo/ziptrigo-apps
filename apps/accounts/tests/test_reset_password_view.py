"""The reset-password page (issue #52): session view replacing the old `fetch()`-based
`POST /api/auth/reset-password` flow. No rate limit here, matching that endpoint -- the token
itself is the unguessable secret."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services.password_reset import PasswordResetService
from apps.accounts.tokens import PasswordResetToken

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def _url(token: str) -> str:
    return reverse('accounts:reset-password', args=[token])


def _reset_token(user: User) -> str:
    # `Token.for_user`'s `cls` typevar is bound to `Token` itself, not a subclass -- ty flags every
    # call site the same way it already flags `PasswordResetService.request_reset` and this file's
    # own API counterpart (`apps/accounts/tests/test_auth.py`), see `apps.accounts.tokens`'s module
    # comments. Centralised here so the suppression exists once, not at every call site below.
    return str(PasswordResetToken.for_user(user))  # ty: ignore[invalid-argument-type]


def test_get_with_valid_token_renders_form(client, user):
    token = _reset_token(user)

    response = client.get(_url(token))

    assert response.status_code == 200
    assert f'hx-post="{_url(token)}"' in response.content.decode()


def test_get_with_invalid_token_renders_expired_page(client):
    response = client.get(_url('not-a-real-token'))

    assert response.status_code == 200
    assert 'expired' in response.content.decode().lower()


def test_valid_submission_updates_password_and_redirects_to_login(client, user):
    token = _reset_token(user)

    response = client.post(
        _url(token), {'password': 'newpass123', 'password_confirm': 'newpass123'}, **HTMX
    )

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:login')
    user.refresh_from_db()
    assert user.check_password('newpass123')


def test_without_htmx_redirects_to_login(client, user):
    token = _reset_token(user)

    response = client.post(
        _url(token), {'password': 'newpass123', 'password_confirm': 'newpass123'}
    )

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:login')


def test_mismatched_passwords_returns_422(client, user):
    token = _reset_token(user)

    response = client.post(
        _url(token), {'password': 'newpass123', 'password_confirm': 'different123'}, **HTMX
    )

    assert response.status_code == 422
    assert 'match' in response.content.decode().lower()
    user.refresh_from_db()
    assert user.check_password('testpass123')


def test_weak_password_returns_422(client, user):
    token = _reset_token(user)

    response = client.post(_url(token), {'password': 'weak', 'password_confirm': 'weak'}, **HTMX)

    assert response.status_code == 422
    user.refresh_from_db()
    assert user.check_password('testpass123')


def test_post_with_expired_token_redirects_to_self_instead_of_a_partial(client, user, monkeypatch):
    """A token that expires between the `GET` and the `POST` must not render the full,
    `core/base.html`-extending expired-link page as an htmx partial -- it should redirect back to
    this same URL so the browser does a real navigation and gets the full page."""
    monkeypatch.setattr(PasswordResetService, 'validate_token', staticmethod(lambda token: None))

    response = client.post(
        _url('some-token'), {'password': 'newpass123', 'password_confirm': 'newpass123'}, **HTMX
    )

    assert response['HX-Redirect'] == _url('some-token')


def test_get_with_expired_token_renders_full_expired_page(client, monkeypatch):
    monkeypatch.setattr(PasswordResetService, 'validate_token', staticmethod(lambda token: None))

    response = client.get(_url('some-token'))

    assert response.status_code == 200
    assert 'expired' in response.content.decode().lower()


def test_reset_password_requires_csrf_token(user):
    from django.test import Client

    token = _reset_token(user)
    client = Client(enforce_csrf_checks=True)

    response = client.post(
        _url(token), {'password': 'newpass123', 'password_confirm': 'newpass123'}
    )

    assert response.status_code == 403
