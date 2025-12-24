"""Custom JWT token classes for email confirmation and password reset."""

from datetime import timedelta

from django.conf import settings
from ninja_jwt.tokens import Token


class EmailConfirmationToken(Token):
    """JWT token for email confirmation links."""

    token_type = 'email_confirmation'
    lifetime = timedelta(hours=settings.EMAIL_CONFIRMATION_TOKEN_TTL_HOURS)  # type: ignore[assignment]


class PasswordResetToken(Token):
    """JWT token for password reset links."""

    token_type = 'password_reset'
    lifetime = timedelta(hours=settings.PASSWORD_RESET_TOKEN_TTL_HOURS)  # type: ignore[assignment]
