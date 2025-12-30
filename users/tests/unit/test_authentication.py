from unittest.mock import Mock

import pytest
from users.users.auth import AdminAuth, JWTAuth
from users.users.models import User
from users.users.tokens import CustomAccessToken

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_jwt_auth_returns_none_without_valid_token():
    request = Mock()

    result = JWTAuth().authenticate(request, '')

    assert result is None


def test_jwt_auth_accepts_valid_token(regular_user: User):
    token = CustomAccessToken.for_user(regular_user)

    request = Mock()
    result = JWTAuth().authenticate(request, str(token))

    assert result is not None
    assert result.id == regular_user.id


def test_jwt_auth_returns_none_for_expired_token(regular_user: User, settings):
    # Create a token with very short lifetime
    settings.NINJA_JWT['ACCESS_TOKEN_LIFETIME'] = __import__('datetime').timedelta(seconds=-1)
    token = CustomAccessToken.for_user(regular_user)

    request = Mock()
    result = JWTAuth().authenticate(request, str(token))

    assert result is None


def test_jwt_auth_returns_none_for_invalid_token():
    request = Mock()

    result = JWTAuth().authenticate(request, 'not-a-jwt')

    assert result is None


def test_jwt_auth_returns_none_for_unknown_user(regular_user: User):
    token = CustomAccessToken.for_user(regular_user)
    # Delete the user after creating the token
    regular_user.delete()

    request = Mock()
    result = JWTAuth().authenticate(request, str(token))

    assert result is None


def test_jwt_auth_returns_none_for_inactive_user(regular_user: User):
    regular_user.status = User.STATUS_INACTIVE
    regular_user.save(update_fields=['status'])

    token = CustomAccessToken.for_user(regular_user)

    request = Mock()
    result = JWTAuth().authenticate(request, str(token))

    assert result is None


def test_admin_auth_accepts_staff_user(admin_user: User):
    token = CustomAccessToken.for_user(admin_user)

    request = Mock()
    result = AdminAuth().authenticate(request, str(token))

    assert result is not None
    assert result.id == admin_user.id


def test_admin_auth_rejects_non_staff_user(regular_user: User):
    token = CustomAccessToken.for_user(regular_user)

    request = Mock()
    result = AdminAuth().authenticate(request, str(token))

    assert result is None
