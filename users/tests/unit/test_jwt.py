import pytest
from ninja_jwt.exceptions import TokenError
from src.users.models import User
from src.users.tokens import CustomAccessToken, CustomRefreshToken

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_custom_access_token_includes_user_claims(regular_user: User):
    token = CustomAccessToken.for_user(regular_user)

    assert isinstance(str(token), str)
    assert token['sub'] == str(regular_user.id)
    assert token['email'] == regular_user.email


def test_custom_refresh_token_creates_access_token(regular_user: User):
    refresh = CustomRefreshToken.for_user(regular_user)
    access = refresh.access_token

    assert isinstance(access, CustomAccessToken)
    assert access['sub'] == str(regular_user.id)
    assert access['email'] == regular_user.email


def test_token_rejects_invalid_signature(settings, regular_user: User):
    token = CustomAccessToken.for_user(regular_user)
    token_str = str(token)

    # Tamper with the token
    tampered_token = token_str[:-5] + 'XXXXX'

    with pytest.raises(TokenError):
        CustomAccessToken(tampered_token)
