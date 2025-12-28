"""
Pytest configuration and fixtures for the QR code project.
"""

import os

import pytest
from django.contrib.auth import get_user_model
from ninja.testing import TestAsyncClient
from rest_framework.test import APIClient  # type: ignore[import-not-found]
from src.qr_code.api.router import api
from src.qr_code.models import QRCode, QRCodeErrorCorrection, QRCodeFormat, QRCodeType
from src.qr_code.tokens import EmailConfirmationToken, PasswordResetToken

# Ensure settings that require env vars have sane defaults during tests.
os.environ.setdefault('EMAIL_BACKENDS', 'console')

# Keep tests independent from host machine configuration.
os.environ.pop('ENVIRONMENT', None)


User = get_user_model()


@pytest.fixture
def api_client():
    """Provide a DRF API client for testing (legacy)."""
    return APIClient()


@pytest.fixture
def ninja_client():
    """Provide a Django Ninja async test client."""
    return TestAsyncClient(api)


@pytest.fixture
def user(db):
    """Create a test user."""
    from datetime import UTC, datetime

    user = User.objects.create_user(
        username='testuser@example.com',
        email='testuser@example.com',
        password='testpass123',
        name='Test User',
    )
    # Mark email as confirmed for backward compatibility with existing tests
    user.email_confirmed = True
    user.email_confirmed_at = datetime.now(UTC)
    user.save()
    return user


@pytest.fixture
def authenticated_client(api_client, user):
    """Provide an authenticated DRF API client (legacy)."""
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def jwt_tokens(user):
    """Generate JWT tokens for a user."""
    from ninja_jwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),  # type: ignore[attr-defined]
        'refresh': str(refresh),
    }


@pytest.fixture
def authenticated_ninja_client(ninja_client, jwt_tokens):
    """Provide an authenticated Django Ninja client with JWT."""

    class AuthenticatedNinjaClient(TestAsyncClient):
        def __init__(self, router, access_token):
            super().__init__(router)
            self.access_token = access_token

        def request(self, method, path, *args, **kwargs):
            """Override request to add Authorization header."""
            headers = kwargs.get('headers', {})
            headers['Authorization'] = f'Bearer {self.access_token}'
            kwargs['headers'] = headers
            return super().request(method, path, *args, **kwargs)

    return AuthenticatedNinjaClient(api, jwt_tokens['access'])


@pytest.fixture
def email_confirmation_token(user):
    """Generate an email confirmation token for a user."""
    token = EmailConfirmationToken.for_user(user)
    return str(token)


@pytest.fixture
def password_reset_token(user):
    """Generate a password reset token for a user."""
    token = PasswordResetToken.for_user(user)
    return str(token)


@pytest.fixture
def qr_code(user):
    """Create a test QR code."""
    return QRCode.objects.create(
        content='https://example.com',
        created_by=user,
        qr_type=QRCodeType.TEXT,
        qr_format=QRCodeFormat.PNG,
        size=10,
        error_correction=QRCodeErrorCorrection.MEDIUM,
        border=4,
        background_color='white',
        foreground_color='black',
        image_file='test.png',
    )


@pytest.fixture
def qr_code_with_shortening(user):
    """Create a test QR code with URL shortening."""
    return QRCode.objects.create(
        content='https://example.com/long-url',
        original_url='https://example.com/long-url',
        use_url_shortening=True,
        created_by=user,
        qr_type=QRCodeType.TEXT,
        qr_format=QRCodeFormat.PNG,
        image_file='test_short.png',
    )
