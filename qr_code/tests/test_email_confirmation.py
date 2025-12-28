"""
Unit and integration tests for email confirmation functionality.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from src.qr_code.models.time_limited_token import TimeLimitedToken
from src.qr_code.services.email_confirmation import (
    EmailConfirmationService,
    get_email_confirmation_service,
)

User = get_user_model()


@pytest.mark.django_db
class TestSignupWithEmailConfirmation:
    """Test cases for signup with email confirmation."""

    @patch('src.qr_code.services.email_confirmation.send_email')
    def test_signup_sends_confirmation_email(self, mock_send_email, api_client):
        """Test that signup sends a confirmation email."""

        url = reverse('signup')
        data = {
            'name': 'John Doe',
            'email': 'john@example.com',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['message'] == (
            'Account created! Please check your email to confirm your address.'
        )
        # Verify email was sent
        mock_send_email.assert_called_once()
        assert mock_send_email.call_args.kwargs['to'] == 'john@example.com'

    def test_signup_creates_unconfirmed_user(self, api_client):
        """Test that signup creates a user with email_confirmed=False."""
        url = reverse('signup')
        data = {
            'name': 'Jane Doe',
            'email': 'jane@example.com',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        user = User.objects.get(email='jane@example.com')
        assert user.email_confirmed is False
        assert user.email_confirmed_at is None

    @patch('src.qr_code.services.email_confirmation.send_email')
    def test_signup_creates_confirmation_token(self, mock_send_email, api_client):
        """Test that signup creates an email confirmation token."""

        url = reverse('signup')
        data = {
            'name': 'Bob Smith',
            'email': 'bob@example.com',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        user = User.objects.get(email='bob@example.com')
        token = TimeLimitedToken.objects.get(
            user=user, token_type=TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )
        assert token is not None
        assert token.used_at is None

    def test_signup_does_not_auto_login(self, api_client):
        """Test that signup does not automatically log in the user."""
        url = reverse('signup')
        data = {
            'name': 'Alice Wonder',
            'email': 'alice@example.com',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert 'sessionid' not in response.data
        assert 'user' not in response.data

        # Verify user cannot access authenticated endpoints
        qrcode_list_url = reverse('qrcode-list')
        auth_response = api_client.get(qrcode_list_url)
        assert auth_response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestLoginWithEmailConfirmation:
    """Test cases for login requiring email confirmation."""

    def test_login_blocks_unconfirmed_user(self, api_client):
        """Test that login is blocked for users with unconfirmed email."""
        # Create unconfirmed user
        user = User.objects.create_user(
            username='unconfirmed@example.com',
            email='unconfirmed@example.com',
            password='password123',
            name='Unconfirmed User',
            email_confirmed=False,
        )
        print(f'User created: {user.email}')

        url = reverse('login')
        data = {'email': 'unconfirmed@example.com', 'password': 'password123'}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'confirm your email address' in response.data['detail'].lower()

    def test_login_allows_confirmed_user(self, api_client):
        """Test that login succeeds for users with confirmed email."""
        # Create confirmed user
        user = User.objects.create_user(
            username='confirmed@example.com',
            email='confirmed@example.com',
            password='password123',
            name='Confirmed User',
            email_confirmed=True,
            email_confirmed_at=datetime.now(UTC),
        )
        print(f'User created: {user.email}')

        url = reverse('login')
        data = {'email': 'confirmed@example.com', 'password': 'password123'}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert 'sessionid' in response.data
        assert response.data['user']['email'] == 'confirmed@example.com'


@pytest.mark.django_db
class TestConfirmEmailEndpoint:
    """Test cases for the confirm email API endpoint."""

    def test_confirm_email_with_valid_token(self, api_client):
        """Test confirming email with a valid token."""
        user = User.objects.create_user(
            username='test@example.com',
            email='test@example.com',
            password='password123',
            name='Test User',
            email_confirmed=False,
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        url = reverse('confirm-email')
        data = {'token': token.token}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['detail'] == 'Email has been confirmed.'

        # Verify user is now confirmed
        user.refresh_from_db()
        assert user.email_confirmed is True
        assert user.email_confirmed_at is not None

        # Verify token is marked as used
        token.refresh_from_db()
        assert token.used_at is not None

    def test_confirm_email_with_invalid_token(self, api_client):
        """Test confirming email with an invalid token."""
        url = reverse('confirm-email')
        data = {'token': 'invalid-token-12345'}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['detail'] == 'Invalid or expired token.'

    def test_confirm_email_with_expired_token(self, api_client):
        """Test confirming email with an expired token."""
        user = User.objects.create_user(
            username='expired@example.com',
            email='expired@example.com',
            password='password123',
            name='Expired User',
            email_confirmed=False,
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        # Manually set token creation time to 49 hours ago (past 48-hour TTL)
        token.created_at = datetime.now(UTC) - timedelta(hours=49)
        token.save()

        url = reverse('confirm-email')
        data = {'token': token.token}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['detail'] == 'Invalid or expired token.'

        # Verify user is still unconfirmed
        user.refresh_from_db()
        assert user.email_confirmed is False

    def test_confirm_email_with_used_token(self, api_client):
        """Test confirming email with an already used token."""
        user = User.objects.create_user(
            username='used@example.com',
            email='used@example.com',
            password='password123',
            name='Used Token User',
            email_confirmed=False,
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        # Mark token as used
        token.used_at = datetime.now(UTC)
        token.save()

        url = reverse('confirm-email')
        data = {'token': token.token}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['detail'] == 'Invalid or expired token.'

    def test_confirm_email_missing_token(self, api_client):
        """Test confirming email without providing a token."""
        url = reverse('confirm-email')
        data: dict = {}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'token' in response.data

    def test_confirm_email_wrong_token_type(self, api_client):
        """Test that password reset tokens cannot be used for email confirmation."""
        user = User.objects.create_user(
            username='wrongtype@example.com',
            email='wrongtype@example.com',
            password='password123',
            name='Wrong Type User',
            email_confirmed=False,
        )
        # Create a password reset token instead
        token = TimeLimitedToken.create_for_user(user, TimeLimitedToken.TOKEN_TYPE_PASSWORD_RESET)

        url = reverse('confirm-email')
        data = {'token': token.token}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['detail'] == 'Invalid or expired token.'


@pytest.mark.django_db
class TestResendConfirmationEndpoint:
    """Test cases for the resend confirmation API endpoint."""

    @patch('src.qr_code.services.email_confirmation.send_email')
    def test_resend_confirmation_for_unconfirmed_user(self, mock_send_email, api_client):
        """Test resending confirmation email for an unconfirmed user."""

        user = User.objects.create_user(
            username='unconfirmed@example.com',
            email='unconfirmed@example.com',
            password='password123',
            name='Unconfirmed User',
            email_confirmed=False,
        )
        print(f'User created: {user.email}')

        url = reverse('resend-confirmation')
        data = {'email': 'unconfirmed@example.com'}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert 'confirmation email will be sent' in response.data['detail'].lower()
        mock_send_email.assert_called_once()

    @patch('src.qr_code.services.email_confirmation.send_email')
    def test_resend_confirmation_for_confirmed_user(self, mock_send_email, api_client):
        """Test that resend does not send email for already confirmed users."""

        user = User.objects.create_user(
            username='confirmed@example.com',
            email='confirmed@example.com',
            password='password123',
            name='Confirmed User',
            email_confirmed=True,
            email_confirmed_at=datetime.now(UTC),
        )
        print(f'User created: {user.email}')

        url = reverse('resend-confirmation')
        data = {'email': 'confirmed@example.com'}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        # Generic message to avoid leaking user existence
        assert 'confirmation email will be sent' in response.data['detail'].lower()
        # But email should not actually be sent
        mock_send_email.assert_not_called()

    @patch('src.qr_code.services.email_confirmation.send_email')
    def test_resend_confirmation_for_nonexistent_user(self, mock_send_email, api_client):
        """Test that resend gives generic response for nonexistent users."""

        url = reverse('resend-confirmation')
        data = {'email': 'nonexistent@example.com'}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        # Generic message to avoid leaking user existence
        assert 'confirmation email will be sent' in response.data['detail'].lower()
        mock_send_email.assert_not_called()

    def test_resend_confirmation_missing_email(self, api_client):
        """Test that resend requires email field."""
        url = reverse('resend-confirmation')
        data: dict = {}

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'email' in response.data


@pytest.mark.django_db
class TestConfirmEmailPage:
    """Test cases for the email confirmation page views."""

    def test_confirm_email_page_with_valid_token(self, client):
        """Test confirmation page redirects to success with valid token."""
        user = User.objects.create_user(
            username='pagetest@example.com',
            email='pagetest@example.com',
            password='password123',
            name='Page Test User',
            email_confirmed=False,
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        url = reverse('confirm-email-page', args=[token.token])
        response = client.get(url)

        # Should redirect to success page
        assert response.status_code == 302
        assert response.url == reverse('email-confirmation-success')

        # Verify user is confirmed
        user.refresh_from_db()
        assert user.email_confirmed is True

    def test_confirm_email_page_with_invalid_token(self, client):
        """Test confirmation page shows expired template with invalid token."""
        url = reverse('confirm-email-page', args=['invalid-token'])
        response = client.get(url)

        assert response.status_code == 200
        assert 'email_confirmation_expired.html' in [t.name for t in response.templates]

    def test_confirm_email_page_with_expired_token(self, client):
        """Test confirmation page shows expired template with expired token."""
        user = User.objects.create_user(
            username='pageexpired@example.com',
            email='pageexpired@example.com',
            password='password123',
            name='Page Expired User',
            email_confirmed=False,
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        # Expire the token
        token.created_at = datetime.now(UTC) - timedelta(hours=49)
        token.save()

        url = reverse('confirm-email-page', args=[token.token])
        response = client.get(url)

        assert response.status_code == 200
        assert 'email_confirmation_expired.html' in [t.name for t in response.templates]

    def test_email_confirmation_success_page(self, client):
        """Test that the success page renders correctly."""
        url = reverse('email-confirmation-success')
        response = client.get(url)

        assert response.status_code == 200
        assert 'email_confirmation_success.html' in [t.name for t in response.templates]


@pytest.mark.django_db
class TestEmailConfirmationService:
    """Test cases for the EmailConfirmationService."""

    @patch('src.qr_code.services.email_confirmation.send_email')
    def test_send_confirmation_email(self, mock_send_email):
        """Test sending a confirmation email."""

        user = User.objects.create_user(
            username='service@example.com',
            email='service@example.com',
            password='password123',
            name='Service Test User',
        )

        service = get_email_confirmation_service()
        service.send_confirmation_email(user)

        # Verify email was sent
        mock_send_email.assert_called_once()
        assert mock_send_email.call_args.kwargs['to'] == 'service@example.com'
        assert 'confirm' in mock_send_email.call_args.kwargs['subject'].lower()

        # Verify token was created
        token = TimeLimitedToken.objects.get(
            user=user, token_type=TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )
        assert token is not None

    def test_validate_token_success(self):
        """Test validating a valid token."""
        user = User.objects.create_user(
            username='validate@example.com',
            email='validate@example.com',
            password='password123',
            name='Validate User',
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        service = EmailConfirmationService(email_backend_classes=[])
        result = service.validate_token(token.token)

        assert result is not None
        assert result.token == token.token

    def test_validate_token_expired(self):
        """Test validating an expired token."""
        user = User.objects.create_user(
            username='expired@example.com',
            email='expired@example.com',
            password='password123',
            name='Expired User',
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        # Expire the token
        token.created_at = datetime.now(UTC) - timedelta(hours=49)
        token.save()

        service = EmailConfirmationService(email_backend_classes=[])
        result = service.validate_token(token.token)

        assert result is None

    def test_confirm_email(self):
        """Test confirming an email."""
        user = User.objects.create_user(
            username='confirm@example.com',
            email='confirm@example.com',
            password='password123',
            name='Confirm User',
            email_confirmed=False,
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        service = EmailConfirmationService(email_backend_classes=[])
        service.confirm_email(token)

        # Verify user is confirmed
        user.refresh_from_db()
        assert user.email_confirmed is True
        assert user.email_confirmed_at is not None

        # Verify token is used
        token.refresh_from_db()
        assert token.used_at is not None


@pytest.mark.django_db
class TestTimeLimitedTokenExpiry:
    """Test token expiry for different token types."""

    def test_email_confirmation_token_ttl(self):
        """Test that email confirmation tokens use correct TTL."""
        user = User.objects.create_user(
            username='ttl@example.com',
            email='ttl@example.com',
            password='password123',
            name='TTL User',
        )
        token = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_EMAIL_CONFIRMATION
        )

        # Token should not be expired immediately
        assert token.is_expired is False

        # Token should be expired after 48 hours
        token.created_at = datetime.now(UTC) - timedelta(hours=48, minutes=1)
        token.save()
        assert token.is_expired is True

    def test_password_reset_token_ttl(self):
        """Test that password reset tokens use correct TTL."""
        user = User.objects.create_user(
            username='resetttl@example.com',
            email='resetttl@example.com',
            password='password123',
            name='Reset TTL User',
        )
        token = TimeLimitedToken.create_for_user(user, TimeLimitedToken.TOKEN_TYPE_PASSWORD_RESET)

        # Token should not be expired immediately
        assert token.is_expired is False

        # Token should be expired after 4 hours
        token.created_at = datetime.now(UTC) - timedelta(hours=4, minutes=1)
        token.save()
        assert token.is_expired is True
