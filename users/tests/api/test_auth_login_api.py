import pytest

from src.users.models import User
from src.users.tokens import CustomAccessToken

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_login_returns_jwt_for_active_user(api_client, regular_user: User):
    response = api_client.post(
        '/auth/login',
        json={'email': regular_user.email, 'password': 'password123'},
    )

    assert response.status_code == 200
    data = response.json()
    assert data['token_type'] == 'Bearer'
    assert data['expires_in'] == 3600
    assert 'access_token' in data
    assert 'refresh_token' in data

    # Decode and verify access token
    token = CustomAccessToken(data['access_token'])
    assert token['email'] == regular_user.email
    assert token['sub'] == str(regular_user.id)


def test_login_rejects_invalid_credentials(api_client, regular_user: User):
    response = api_client.post(
        '/auth/login',
        json={'email': regular_user.email, 'password': 'wrong'},
    )

    assert response.status_code == 400
    assert response.json()['detail'] == 'Invalid credentials'


def test_login_rejects_inactive_user(api_client, regular_user: User):
    regular_user.status = User.STATUS_INACTIVE
    regular_user.save(update_fields=['status'])

    response = api_client.post(
        '/auth/login',
        json={'email': regular_user.email, 'password': 'password123'},
    )

    assert response.status_code == 403
    assert response.json()['detail'] == 'User not active'
