import pytest
from django.contrib.auth import SESSION_KEY
from django.urls import reverse

from apps.accounts.backends import EmailBackend
from apps.accounts.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_email_backend_authenticate_success(regular_user: User):
    backend = EmailBackend()

    user = backend.authenticate(None, email=regular_user.email, password='password123')

    assert user is not None
    assert user.id == regular_user.id


def test_email_backend_authenticate_wrong_password_returns_none(regular_user: User):
    backend = EmailBackend()

    user = backend.authenticate(None, email=regular_user.email, password='wrong')

    assert user is None


def test_email_backend_authenticate_missing_credentials_returns_none():
    backend = EmailBackend()

    assert backend.authenticate(None, email=None, password='x') is None
    assert backend.authenticate(None, email='user@example.com', password=None) is None


def test_email_backend_get_user_returns_user(regular_user: User):
    backend = EmailBackend()

    user = backend.get_user(str(regular_user.id))

    assert user is not None
    assert user.id == regular_user.id


def test_email_backend_get_user_unknown_returns_none():
    backend = EmailBackend()

    assert backend.get_user('00000000-0000-0000-0000-000000000000') is None


def _deactivate(user: User) -> None:
    """What unticking "Active" in the user admin does."""
    User.objects.filter(pk=user.pk).update(is_active=False)


def test_unknown_email_still_runs_the_password_hasher(monkeypatch):
    """Without it, the login's response time would reveal which addresses have an account."""
    hashed: list[str] = []
    monkeypatch.setattr(User, 'set_password', lambda self, raw: hashed.append(raw))

    assert EmailBackend().authenticate(None, email='nobody@example.com', password='x1') is None
    assert hashed == ['x1']


def test_inactive_user_cannot_authenticate(regular_user: User):
    _deactivate(regular_user)

    assert (
        EmailBackend().authenticate(None, email=regular_user.email, password='password123') is None
    )
    assert EmailBackend().get_user(str(regular_user.id)) is None


def test_inactive_user_cannot_log_in_on_the_web(client, user: User):
    _deactivate(user)

    response = client.post(
        reverse('accounts:login'), {'email': user.email, 'password': 'testpass123'}
    )

    assert response.status_code == 422
    assert SESSION_KEY not in client.session


def test_inactive_user_cannot_log_in_on_the_api(api_client, regular_user: User):
    _deactivate(regular_user)

    response = api_client.post(
        '/auth/login', json={'email': regular_user.email, 'password': 'password123'}
    )

    assert response.status_code == 400


def test_deactivating_a_user_ends_their_existing_session(client, user: User):
    client.force_login(user, backend='apps.accounts.backends.EmailBackend')
    assert client.get(reverse('accounts:account')).status_code == 200

    _deactivate(user)

    response = client.get(reverse('accounts:account'))
    assert response.status_code == 302
    assert reverse('accounts:login') in response['Location']
