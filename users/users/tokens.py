from datetime import timedelta

from django.conf import settings
from ninja_jwt.tokens import Token

from .models import User


class CustomAccessToken(Token):
    """Custom access token."""

    token_type = 'access'
    lifetime_setting = 'ACCESS_TOKEN_LIFETIME'

    @classmethod
    def for_user(cls, user: User) -> 'CustomAccessToken':  # type: ignore[override]
        """Create a token for the given user with basic claims."""
        token = super().for_user(user)
        token['email'] = user.email
        return token  # type: ignore


class CustomRefreshToken(Token):
    """Custom refresh token."""

    token_type = 'refresh'
    lifetime_setting = 'REFRESH_TOKEN_LIFETIME'
    no_copy_claims = (
        'token_type',
        'exp',
        'iat',
        'jti',
    )

    @property
    def access_token(self) -> CustomAccessToken:
        """Get an access token from this refresh token."""
        access = CustomAccessToken()

        # Copy claims from refresh token (except no_copy_claims)
        for claim, value in self.payload.items():
            if claim in self.no_copy_claims:
                continue
            access[claim] = value

        return access

    @classmethod
    def for_user(cls, user: User) -> 'CustomRefreshToken':  # type: ignore[override]
        """Create a refresh token for the given user."""
        token = super().for_user(user)
        token['email'] = user.email

        return token  # type: ignore


class EmailConfirmationToken(Token):
    """JWT token for email confirmation links."""

    token_type = 'email_confirmation'
    lifetime = timedelta(hours=settings.EMAIL_CONFIRMATION_TOKEN_TTL_HOURS)  # type: ignore[assignment]


class PasswordResetToken(Token):
    """JWT token for password reset links."""

    token_type = 'password_reset'
    lifetime = timedelta(hours=settings.PASSWORD_RESET_TOKEN_TTL_HOURS)  # type: ignore[assignment]
