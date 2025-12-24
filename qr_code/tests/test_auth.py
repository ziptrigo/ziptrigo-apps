"""
Unit and integration tests for authentication endpoints.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from src.qr_code.models.time_limited_token import TimeLimitedToken
from src.qr_code.services.password_reset import PasswordResetService

User = get_user_model()


@pytest.mark.django_db
class TestSignupEndpoint:
    """Test cases for the signup endpoint."""

    def test_signup_success(self, api_client):
        """Test successful user signup."""
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
        assert User.objects.filter(email='john@example.com').exists()

    def test_signup_does_not_create_session(self, api_client):
        """Test that signup does not create a session (requires email confirmation first)."""
        url = reverse('signup')
        data = {
            'name': 'Jane Doe',
            'email': 'jane@example.com',
            'password': 'secure123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert 'sessionid' not in response.data
        assert 'user' not in response.data

    def test_signup_password_too_short(self, api_client):
        """Test that signup rejects passwords shorter than 6 characters."""
        url = reverse('signup')
        data = {
            'name': 'Bob Smith',
            'email': 'bob@example.com',
            'password': 'pass1',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'password' in response.data

    def test_signup_password_no_digit(self, api_client):
        """Test that signup rejects passwords without a digit."""
        url = reverse('signup')
        data = {
            'name': 'Alice Wonder',
            'email': 'alice@example.com',
            'password': 'password',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'password' in response.data

    def test_signup_duplicate_email(self, api_client, user):
        """Test that signup rejects duplicate email addresses."""
        url = reverse('signup')
        data = {
            'name': 'Another User',
            'email': user.email,
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['message'] == 'User with that email already exists.'

    def test_signup_missing_name(self, api_client):
        """Test that signup requires name field."""
        url = reverse('signup')
        data = {
            'email': 'test@example.com',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'name' in response.data

    def test_signup_missing_email(self, api_client):
        """Test that signup requires email field."""
        url = reverse('signup')
        data = {
            'name': 'Test User',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'email' in response.data

    def test_signup_missing_password(self, api_client):
        """Test that signup requires password field."""
        url = reverse('signup')
        data = {
            'name': 'Test User',
            'email': 'test@example.com',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'password' in response.data

    def test_signup_invalid_email(self, api_client):
        """Test that signup rejects invalid email format."""
        url = reverse('signup')
        data = {
            'name': 'Test User',
            'email': 'not-an-email',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'email' in response.data

    def test_signup_user_not_logged_in(self, api_client):
        """Test that user is NOT automatically logged in after signup."""
        url = reverse('signup')
        data = {
            'name': 'Not Logged In User',
            'email': 'notloggedin@example.com',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        # Verify user CANNOT access authenticated endpoints
        qrcode_list_url = reverse('qrcode-list')
        auth_response = api_client.get(qrcode_list_url)
        assert auth_response.status_code == status.HTTP_403_FORBIDDEN

    def test_signup_password_with_special_chars(self, api_client):
        """Test that signup accepts passwords with special characters."""
        url = reverse('signup')
        data = {
            'name': 'Special User',
            'email': 'special@example.com',
            'password': 'p@ssw0rd!',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        assert User.objects.filter(email='special@example.com').exists()

    def test_signup_user_cannot_login_without_confirmation(self, api_client):
        """Test that newly created user cannot log in without email confirmation."""
        from datetime import UTC, datetime

        signup_url = reverse('signup')
        signup_data = {
            'name': 'New User',
            'email': 'newuser@example.com',
            'password': 'password123',
        }

        # Signup
        signup_response = api_client.post(signup_url, signup_data, format='json')
        assert signup_response.status_code == status.HTTP_201_CREATED

        # Try to login without confirming email
        login_url = reverse('login')
        login_data = {
            'email': 'newuser@example.com',
            'password': 'password123',
        }
        login_response = api_client.post(login_url, login_data, format='json')

        assert login_response.status_code == status.HTTP_403_FORBIDDEN
        assert 'confirm your email' in login_response.data['detail'].lower()

        # Manually confirm email for second part of test
        user = User.objects.get(email='newuser@example.com')
        user.email_confirmed = True
        user.email_confirmed_at = datetime.now(UTC)
        user.save()

        # Now login should work
        login_response = api_client.post(login_url, login_data, format='json')
        assert login_response.status_code == status.HTTP_200_OK
        assert login_response.data['user']['email'] == 'newuser@example.com'


@pytest.mark.django_db
class TestHomeRedirect:
    """Tests for home page behavior depending on authentication state."""

    def test_home_redirects_authenticated_user_to_dashboard(self, client, user):
        """Authenticated users visiting / should be redirected to /dashboard/."""
        assert client.login(username=user.username, password='testpass123') is True

        response = client.get('/')

        assert response.status_code == 302
        assert response.url == '/dashboard/'


@pytest.mark.django_db
class TestLoginEndpoint:
    """Test cases for the login endpoint."""

    def test_login_success(self, api_client, user):
        """Test successful user login."""
        url = reverse('login')
        data = {
            'email': user.email,
            'password': 'testpass123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert 'user' in response.data
        assert 'sessionid' in response.data
        assert response.data['user']['email'] == user.email
        assert response.data['user']['name'] == user.name

    def test_login_creates_session(self, api_client, user):
        """Test that login creates a session."""
        url = reverse('login')
        data = {
            'email': user.email,
            'password': 'testpass123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['sessionid'] is not None
        assert len(response.data['sessionid']) > 0

    def test_login_wrong_password(self, api_client, user):
        """Test login with wrong password fails."""
        url = reverse('login')
        data = {
            'email': user.email,
            'password': 'wrongpassword',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'detail' in response.data

    def test_login_nonexistent_email(self, api_client):
        """Test login with non-existent email fails."""
        url = reverse('login')
        data = {
            'email': 'nonexistent@example.com',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'detail' in response.data

    def test_login_missing_email(self, api_client):
        """Test that login requires email field."""
        url = reverse('login')
        data = {
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'email' in response.data

    def test_login_missing_password(self, api_client):
        """Test that login requires password field."""
        url = reverse('login')
        data = {
            'email': 'test@example.com',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'password' in response.data

    def test_login_invalid_email(self, api_client):
        """Test that login rejects invalid email format."""
        url = reverse('login')
        data = {
            'email': 'not-an-email',
            'password': 'password123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'email' in response.data

    def test_login_user_authenticated(self, api_client, user):
        """Test that user is authenticated after login."""
        url = reverse('login')
        data = {
            'email': user.email,
            'password': 'testpass123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        # Verify user can access authenticated endpoints
        qrcode_list_url = reverse('qrcode-list')
        auth_response = api_client.get(qrcode_list_url)
        assert auth_response.status_code == status.HTTP_200_OK

    def test_login_case_sensitive_email(self, api_client, user):
        """Test that login is case-sensitive for email."""
        url = reverse('login')
        # Use uppercase email
        data = {
            'email': user.email.upper(),
            'password': 'testpass123',
        }

        response = api_client.post(url, data, format='json')

        # This should fail since email lookup is case-sensitive in Django
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_returns_user_id(self, api_client, user):
        """Test that login returns user ID."""
        url = reverse('login')
        data = {
            'email': user.email,
            'password': 'testpass123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['user']['id'] == user.id


@pytest.mark.django_db
class TestAuthenticationFlow:
    """Test cases for complete authentication flows."""

    def test_full_signup_confirm_login_and_qrcode_creation_flow(self, api_client):
        """Test complete flow: signup -> confirm email -> login -> create QR code."""
        from datetime import UTC, datetime

        # Signup
        signup_url = reverse('signup')
        signup_data = {
            'name': 'QR Creator',
            'email': 'qrcreator@example.com',
            'password': 'password123',
        }
        signup_response = api_client.post(signup_url, signup_data, format='json')
        assert signup_response.status_code == status.HTTP_201_CREATED

        # Manually confirm email (simulating user clicking confirmation link)
        user = User.objects.get(email='qrcreator@example.com')
        user.email_confirmed = True
        user.email_confirmed_at = datetime.now(UTC)
        user.save()

        # Login
        login_url = reverse('login')
        login_data = {
            'email': 'qrcreator@example.com',
            'password': 'password123',
        }
        login_response = api_client.post(login_url, login_data, format='json')
        assert login_response.status_code == status.HTTP_200_OK

        # Create QR code (should be authenticated from login)
        qrcode_url = reverse('qrcode-list')
        qrcode_data = {
            'url': 'https://example.com',
            'qr_type': 'text',
            'qr_format': 'png',
        }
        qrcode_response = api_client.post(qrcode_url, qrcode_data, format='json')

        assert qrcode_response.status_code == status.HTTP_201_CREATED
        assert 'id' in qrcode_response.data

    def test_full_login_and_qrcode_creation_flow(self, api_client, user):
        """Test complete flow: login -> create QR code."""
        # Login
        login_url = reverse('login')
        login_data = {
            'email': user.email,
            'password': 'testpass123',
        }
        login_response = api_client.post(login_url, login_data, format='json')
        assert login_response.status_code == status.HTTP_200_OK

        # Create QR code (should be authenticated from login)
        qrcode_url = reverse('qrcode-list')
        qrcode_data = {
            'url': 'https://example.com',
            'qr_type': 'text',
            'qr_format': 'png',
        }
        qrcode_response = api_client.post(qrcode_url, qrcode_data, format='json')

        assert qrcode_response.status_code == status.HTTP_201_CREATED

    def test_unauthenticated_cannot_create_qrcode(self, api_client):
        """Test that unauthenticated users cannot create QR codes."""
        qrcode_url = reverse('qrcode-list')
        qrcode_data = {
            'url': 'https://example.com',
            'qr_format': 'png',
        }

        response = api_client.post(qrcode_url, qrcode_data, format='json')

        # Session auth may return 403 (CSRF) or 401 (no session)
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_multiple_users_separate_qrcodes(self, api_client):
        """Test that different users have separate QR codes."""
        from datetime import UTC, datetime

        # Create first user
        signup_url = reverse('signup')
        user1_data = {
            'name': 'User One',
            'email': 'user1@example.com',
            'password': 'password123',
        }
        signup_response1 = api_client.post(signup_url, user1_data, format='json')
        assert signup_response1.status_code == status.HTTP_201_CREATED

        # Confirm and login first user
        user1 = User.objects.get(email='user1@example.com')
        user1.email_confirmed = True
        user1.email_confirmed_at = datetime.now(UTC)
        user1.save()

        login_url = reverse('login')
        login1 = api_client.post(
            login_url, {'email': 'user1@example.com', 'password': 'password123'}, format='json'
        )
        assert login1.status_code == status.HTTP_200_OK

        # Create QR code for user1
        qrcode_url = reverse('qrcode-list')
        qrcode1_data = {
            'url': 'https://user1.com',
            'qr_type': 'text',
            'qr_format': 'png',
        }
        qrcode_response1 = api_client.post(qrcode_url, qrcode1_data, format='json')
        assert qrcode_response1.status_code == status.HTTP_201_CREATED
        qrcode1_id = qrcode_response1.data['id']

        # Create new client for second user
        client2 = api_client.__class__()
        user2_data = {
            'name': 'User Two',
            'email': 'user2@example.com',
            'password': 'password123',
        }
        signup_response2 = client2.post(signup_url, user2_data, format='json')
        assert signup_response2.status_code == status.HTTP_201_CREATED

        # Confirm and login second user
        user2 = User.objects.get(email='user2@example.com')
        user2.email_confirmed = True
        user2.email_confirmed_at = datetime.now(UTC)
        user2.save()

        login2 = client2.post(
            login_url, {'email': 'user2@example.com', 'password': 'password123'}, format='json'
        )
        assert login2.status_code == status.HTTP_200_OK

        # Create QR code for user2
        qrcode2_data = {
            'url': 'https://user2.com',
            'qr_type': 'text',
            'qr_format': 'png',
        }
        qrcode_response2 = client2.post(qrcode_url, qrcode2_data, format='json')
        assert qrcode_response2.status_code == status.HTTP_201_CREATED
        qrcode2_id = qrcode_response2.data['id']

        # Verify users only see their own QR codes
        list_response1 = api_client.get(qrcode_url)
        list_response2 = client2.get(qrcode_url)

        qrcode1_ids = [qr['id'] for qr in list_response1.data]
        qrcode2_ids = [qr['id'] for qr in list_response2.data]

        assert qrcode1_id in qrcode1_ids
        assert qrcode1_id not in qrcode2_ids
        assert qrcode2_id in qrcode2_ids
        assert qrcode2_id not in qrcode1_ids


@pytest.mark.django_db
class TestPasswordResetFlow:
    """Tests for forgot-password and reset-password endpoints."""

    def _setup_fake_backend(self, monkeypatch):
        calls: list[dict[str, str]] = []

        class FakeBackend:
            def send_email(
                self,
                to: str,
                subject: str,
                text_body: str,
                html_body: str | None = None,
            ):
                calls.append(
                    {
                        'to': to,
                        'subject': subject,
                        'text': text_body,
                        'html': html_body or '',
                    }
                )

        def _get_service() -> PasswordResetService:
            return PasswordResetService(email_backend_classes=[FakeBackend])

        # Patch the helper used by the API module so SES is never touched.
        monkeypatch.setattr(
            'src.qr_code.api.auth._get_password_reset_service',
            _get_service,
        )

        return calls

    def test_forgot_password_existing_email_creates_token_and_sends_email(
        self,
        api_client,
        user,
        monkeypatch,
    ):
        calls = self._setup_fake_backend(monkeypatch)
        url = reverse('forgot-password')

        response = api_client.post(url, {'email': user.email}, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert 'detail' in response.data
        assert (
            TimeLimitedToken.objects.filter(
                user=user, token_type=TimeLimitedToken.TOKEN_TYPE_PASSWORD_RESET
            ).count()
            == 1
        )  # type: ignore
        assert len(calls) == 1
        assert calls[0]['to'] == user.email

    def test_forgot_password_nonexistent_email_is_generic_and_creates_no_token(
        self,
        api_client,
        monkeypatch,
    ):
        calls = self._setup_fake_backend(monkeypatch)
        url = reverse('forgot-password')

        response = api_client.post(
            url,
            {'email': 'no-such-user@example.com'},
            format='json',
        )

        assert response.status_code == status.HTTP_200_OK
        assert 'detail' in response.data
        assert TimeLimitedToken.objects.count() == 0  # type: ignore
        assert len(calls) == 0

    def test_forgot_password_missing_email_returns_400(self, api_client):
        url = reverse('forgot-password')
        response = api_client.post(url, {}, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'email' in response.data

    def test_reset_password_success(self, api_client, user):
        token_obj = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_PASSWORD_RESET
        )
        url = reverse('reset-password')
        data = {
            'token': token_obj.token,
            'password': 'newpass123',
            'password_confirm': 'newpass123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()

        # Verify the user can log in with the new password via the API.
        login_url = reverse('login')
        login_response = api_client.post(
            login_url,
            {'email': user.email, 'password': 'newpass123'},
            format='json',
        )
        assert login_response.status_code == status.HTTP_200_OK

    def test_reset_password_invalid_token(self, api_client):
        url = reverse('reset-password')
        data = {
            'token': 'invalid-token',
            'password': 'newpass123',
            'password_confirm': 'newpass123',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'detail' in response.data

    def test_reset_password_mismatched_passwords(self, api_client, user):
        token_obj = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_PASSWORD_RESET
        )
        url = reverse('reset-password')
        data = {
            'token': token_obj.token,
            'password': 'onepass',
            'password_confirm': 'otherpass',
        }

        response = api_client.post(url, data, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'password_confirm' in response.data

    def test_reset_password_token_cannot_be_reused(self, api_client, user):
        token_obj = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_PASSWORD_RESET
        )
        url = reverse('reset-password')
        data = {
            'token': token_obj.token,
            'password': 'newpass123',
            'password_confirm': 'newpass123',
        }

        first = api_client.post(url, data, format='json')
        assert first.status_code == status.HTTP_200_OK

        second = api_client.post(url, data, format='json')
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_reset_token_expiration_property(self, user, settings):
        settings.PASSWORD_RESET_TOKEN_TTL_HOURS = 1
        token_obj = TimeLimitedToken.create_for_user(
            user, TimeLimitedToken.TOKEN_TYPE_PASSWORD_RESET
        )

        from datetime import timedelta

        from django.utils import timezone

        token_obj.created_at = timezone.now() - timedelta(hours=2)
        token_obj.save(update_fields=['created_at'])

        assert token_obj.is_expired is True
