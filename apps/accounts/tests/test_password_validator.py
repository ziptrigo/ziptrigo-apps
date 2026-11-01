"""`PasswordValidator`: the one set of rules for signup, reset, change password, the API and
`inv users create`."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.tokens import PasswordResetToken
from apps.accounts.validators import MAX_PASSWORD_LENGTH, PasswordValidator

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

TOO_LONG = 'Password must be at most 128 characters long.'
LONG_PASSWORD = 'a1' * 65  # 130 characters


def test_the_longest_allowed_password_passes_and_one_more_does_not():
    PasswordValidator().validate('a' * (MAX_PASSWORD_LENGTH - 1) + '1')

    with pytest.raises(ValidationError, match='at most 128'):
        PasswordValidator().validate('a' * MAX_PASSWORD_LENGTH + '1')


def test_register_refuses_an_over_long_password(client):
    response = client.post(
        reverse('accounts:register'),
        {'name': 'New User', 'email': 'new@example.com', 'password': LONG_PASSWORD},
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    assert TOO_LONG in response.content.decode()


def test_reset_page_refuses_an_over_long_password(client, user):
    token = PasswordResetToken.for_user(user)

    response = client.post(
        reverse('accounts:reset-password', args=[str(token)]),
        {'password': LONG_PASSWORD, 'password_confirm': LONG_PASSWORD},
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 422
    assert TOO_LONG in response.content.decode()
    user.refresh_from_db()
    assert user.check_password('testpass123')


def test_api_reset_refuses_an_over_long_password(client, user):
    token = PasswordResetToken.for_user(user)

    response = client.post(
        '/api/auth/reset-password',
        {'token': str(token), 'password': LONG_PASSWORD, 'password_confirm': LONG_PASSWORD},
        content_type='application/json',
    )

    assert response.status_code == 422
    assert TOO_LONG in response.content.decode()
