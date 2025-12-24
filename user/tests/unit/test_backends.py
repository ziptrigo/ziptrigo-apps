import pytest

from src.user.backends import EmailBackend
from src.user.models import User

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
