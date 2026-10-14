import pytest
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.hashers import get_hasher
from django.urls import reverse

from apps.accounts.backends import EmailBackend
from apps.accounts.models import User
from apps.accounts.tokens import CustomRefreshToken

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


@pytest.fixture
def hash_calls(monkeypatch) -> list[str]:
    """Every call to the default password hasher's `encode`, which both making and verifying a
    password go through, so it counts the hashing work whichever way Django reaches it."""
    hasher = get_hasher('default')
    real_encode = hasher.encode
    calls: list[str] = []

    def encode(password, *args, **kwargs):
        calls.append(password)
        return real_encode(password, *args, **kwargs)

    monkeypatch.setattr(hasher, 'encode', encode)
    return calls


def test_unknown_email_still_runs_the_password_hasher(hash_calls):
    """Without it, the login's response time would reveal which addresses have an account."""
    assert EmailBackend().authenticate(None, email='nobody@example.com', password='x1') is None
    assert len(hash_calls) == 1


def test_known_email_with_a_wrong_password_hashes_once_too(regular_user: User, hash_calls):
    assert EmailBackend().authenticate(None, email=regular_user.email, password='wrong') is None
    assert len(hash_calls) == 1


def test_unknown_email_reaches_the_hasher_on_the_api_login(api_client, hash_calls):
    """Guards against an early return before `authenticate()` in the view."""
    response = api_client.post(
        '/auth/login', json={'email': 'nobody@example.com', 'password': 'x1'}
    )

    assert response.status_code == 400
    assert len(hash_calls) == 1


def test_unknown_email_reaches_the_hasher_on_the_web_login(client, hash_calls):
    response = client.post(
        reverse('accounts:login'), {'email': 'nobody@example.com', 'password': 'x1'}
    )

    assert response.status_code == 422
    assert len(hash_calls) == 1


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


def test_refresh_refuses_an_inactive_user(api_client, regular_user: User):
    refresh = str(CustomRefreshToken.for_user(regular_user))
    assert api_client.post('/auth/refresh', json={'refresh_token': refresh}).status_code == 200

    _deactivate(regular_user)

    response = api_client.post('/auth/refresh', json={'refresh_token': refresh})
    assert response.status_code == 403
    assert response.json()['detail'] == 'User not active'
