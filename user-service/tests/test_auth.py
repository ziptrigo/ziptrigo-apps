"""Unit and integration tests for authentication endpoints."""

import pytest
from django.contrib.auth import get_user_model
from ninja.testing import TestClient

from users.api import api

User = get_user_model()


@pytest.fixture
def api_client():
    """Return a test client for the API."""
    return TestClient(api)


@pytest.fixture
def user(db):
    """Create a test user with confirmed email."""
    from datetime import UTC, datetime

    user = User.objects.create_user(
        email='test@example.com',
        password='testpass123',
        name='Test User',
    )
    user.email_confirmed = True
    user.email_confirmed_at = datetime.now(UTC)
    user.save()
    return user


@pytest.mark.django_db
class TestSignupEndpoint:
    """Test cases for the signup endpoint."""

    def test_signup_success(self, api_client):
        """Test successful user signup."""
        data = {
            'name': 'John Doe',
            'email': 'john@example.com',
            'password': 'password123',
        }

        response = api_client.post('/auth/signup', json=data)

        assert response.status_code == 201
        assert response.json()['message'] == (
            'Account created! Please check your email to confirm your address.'
        )
        assert User.objects.filter(email='john@example.com').exists()

    def test_signup_password_too_short(self, api_client):
        """Test that signup rejects passwords shorter than 6 characters."""
        data = {
            'name': 'Bob Smith',
            'email': 'bob@example.com',
            'password': 'pass1',
        }

        response = api_client.post('/auth/signup', json=data)

        assert response.status_code == 422  # Validation error
        assert 'password' in str(response.json())

    def test_signup_password_no_digit(self, api_client):
        """Test that signup rejects passwords without a digit."""
        data = {
            'name': 'Alice Wonder',
            'email': 'alice@example.com',
            'password': 'password',
        }

        response = api_client.post('/auth/signup', json=data)

        assert response.status_code == 422  # Validation error
        assert 'password' in str(response.json())

    def test_signup_duplicate_email(self, api_client, user):
        """Test that signup rejects duplicate email addresses."""
        data = {
            'name': 'Another User',
            'email': user.email,
            'password': 'password123',
        }

        response = api_client.post('/auth/signup', json=data)

        assert response.status_code == 400
        assert 'already exists' in response.json()['detail'].lower()

    def test_signup_user_not_confirmed(self, api_client):
        """Test that newly created user has email_confirmed = False."""
        data = {
            'name': 'New User',
            'email': 'newuser@example.com',
            'password': 'password123',
        }

        response = api_client.post('/auth/signup', json=data)
        assert response.status_code == 201

        user = User.objects.get(email='newuser@example.com')
        assert user.email_confirmed is False
        assert user.email_confirmed_at is None


@pytest.mark.django_db
class TestLoginEndpoint:
    """Test cases for the login endpoint."""

    def test_login_success(self, api_client, user):
        """Test successful user login."""
        data = {
            'email': user.email,
            'password': 'testpass123',
        }

        response = api_client.post('/auth/login', json=data)

        assert response.status_code == 200
        assert 'access_token' in response.json()
        assert 'refresh_token' in response.json()

    def test_login_wrong_password(self, api_client, user):
        """Test login with wrong password fails."""
        data = {
            'email': user.email,
            'password': 'wrongpassword',
        }

        response = api_client.post('/auth/login', json=data)

        assert response.status_code == 400
        assert 'detail' in response.json()

    def test_login_nonexistent_email(self, api_client):
        """Test login with non-existent email fails."""
        data = {
            'email': 'nonexistent@example.com',
            'password': 'password123',
        }

        response = api_client.post('/auth/login', json=data)

        assert response.status_code == 400

    def test_login_unconfirmed_email(self, api_client, db):
        """Test that users cannot log in without confirming email."""
        user = User.objects.create_user(
            email='unconfirmed@example.com',
            password='password123',
            name='Unconfirmed User',
        )
        # email_confirmed defaults to False

        data = {
            'email': user.email,
            'password': 'password123',
        }

        response = api_client.post('/auth/login', json=data)

        assert response.status_code == 403
        assert 'confirm your email' in response.json()['detail'].lower()


@pytest.mark.django_db
class TestPasswordResetFlow:
    """Tests for forgot-password and reset-password endpoints."""

    def test_forgot_password_existing_email(self, api_client, user):
        """Test that forgot password endpoint accepts valid email."""
        data = {'email': user.email}

        response = api_client.post('/auth/forgot-password', json=data)

        assert response.status_code == 200
        assert 'message' in response.json()

    def test_forgot_password_nonexistent_email(self, api_client):
        """Test that forgot password doesn't reveal if email doesn't exist."""
        data = {'email': 'no-such-user@example.com'}

        response = api_client.post('/auth/forgot-password', json=data)

        # Should still return 200 to not reveal email existence
        assert response.status_code == 200
        assert 'message' in response.json()

    def test_reset_password_invalid_token(self, api_client):
        """Test reset password with invalid token."""
        data = {
            'token': 'invalid-token',
            'password': 'newpass123',
            'password_confirm': 'newpass123',
        }

        response = api_client.post('/auth/reset-password', json=data)

        assert response.status_code == 400

    def test_reset_password_mismatched_passwords(self, api_client, user):
        """Test that reset password rejects mismatched passwords."""
        from users.tokens import PasswordResetToken

        token = PasswordResetToken.for_user(user)

        data = {
            'token': str(token),
            'password': 'onepass123',
            'password_confirm': 'otherpass123',
        }

        response = api_client.post('/auth/reset-password', json=data)

        assert response.status_code == 400
        assert 'match' in response.json()['detail'].lower()


@pytest.mark.django_db
class TestEmailConfirmation:
    """Tests for email confirmation endpoints."""

    def test_confirm_email_valid_token(self, api_client, db):
        """Test email confirmation with valid token."""
        from users.tokens import EmailConfirmationToken

        user = User.objects.create_user(
            email='confirm@example.com',
            password='password123',
            name='Confirm User',
        )

        token = EmailConfirmationToken.for_user(user)
        data = {'token': str(token)}

        response = api_client.post('/auth/confirm-email', json=data)

        assert response.status_code == 200

        user.refresh_from_db()
        assert user.email_confirmed is True
        assert user.email_confirmed_at is not None

    def test_confirm_email_invalid_token(self, api_client):
        """Test email confirmation with invalid token."""
        data = {'token': 'invalid-token'}

        response = api_client.post('/auth/confirm-email', json=data)

        assert response.status_code == 400
