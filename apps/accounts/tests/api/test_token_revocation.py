"""Login tokens are bound to the password they were issued under (`tokens.PASSWORD_CLAIM`): any
password change revokes every access and refresh token issued before it."""

import pytest
from asgiref.sync import sync_to_async
from django.test import RequestFactory

from apps.accounts.auth import AdminAuth, AsyncJWTAuth, JWTAuth
from apps.accounts.tokens import PASSWORD_CLAIM, CustomAccessToken, CustomRefreshToken

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def _request():
    return RequestFactory().get('/api/account')


def _change_password(user, password='changed789'):
    user.set_password(password)
    user.save(update_fields=['password'])


def test_access_token_works_until_the_password_changes(user):
    token = str(CustomAccessToken.for_user(user))
    assert JWTAuth().authenticate(_request(), token) == user

    _change_password(user)

    assert JWTAuth().authenticate(_request(), token) is None


def test_token_issued_after_the_change_works(user):
    _change_password(user)

    token = str(CustomAccessToken.for_user(user))

    assert JWTAuth().authenticate(_request(), token) == user


def test_setting_an_unusable_password_revokes_tokens(user):
    token = str(CustomAccessToken.for_user(user))

    user.set_unusable_password()
    user.save()

    assert JWTAuth().authenticate(_request(), token) is None


def test_token_without_the_claim_is_rejected(user):
    """Tokens issued before the claim existed (or forged without it) don't authenticate."""
    token = CustomAccessToken.for_user(user)
    del token[PASSWORD_CLAIM]

    assert JWTAuth().authenticate(_request(), str(token)) is None


def test_admin_auth_is_revoked_too(user):
    user.is_staff = True
    user.save()
    token = str(CustomAccessToken.for_user(user))
    assert AdminAuth().authenticate(_request(), token) == user

    _change_password(user)

    assert AdminAuth().authenticate(_request(), token) is None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_async_auth_is_revoked_too(user):
    token = str(await sync_to_async(CustomAccessToken.for_user)(user))
    assert await AsyncJWTAuth().authenticate(_request(), token) == user

    await sync_to_async(_change_password)(user)

    assert await AsyncJWTAuth().authenticate(_request(), token) is None


def test_api_request_with_a_revoked_token_is_401(client, user):
    token = str(CustomAccessToken.for_user(user))
    _change_password(user)

    response = client.put(
        '/api/account',
        {'name': 'X'},
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )

    assert response.status_code == 401


def _refresh(client, refresh_token):
    return client.post(
        '/api/auth/refresh', {'refresh_token': refresh_token}, content_type='application/json'
    )


def test_refresh_works_until_the_password_changes(client, user):
    refresh = str(CustomRefreshToken.for_user(user))
    assert _refresh(client, refresh).status_code == 200

    _change_password(user)

    assert _refresh(client, refresh).status_code == 401
