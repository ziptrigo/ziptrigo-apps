from datetime import timedelta

from django.conf import settings
from ninja_jwt.settings import api_settings
from ninja_jwt.tokens import Token

from .models import User


class CustomAccessToken(Token):
    """Custom access token."""

    token_type = 'access'
    lifetime = api_settings.ACCESS_TOKEN_LIFETIME

    @classmethod
    # Narrows `Token.for_user`'s `user: AbstractBaseUser` to this service's own concrete `User`,
    # which is technically an LSP violation ty (correctly) flags -- but this project only ever
    # has the one concrete user model, so the narrower signature is deliberate, not a bug.
    def for_user(cls, user: User) -> 'CustomAccessToken':  # ty: ignore[invalid-method-override]
        """Create a token for the given user with basic claims."""
        token = super().for_user(user)
        token['email'] = user.email
        return token  # type: ignore


class CustomRefreshToken(Token):
    """Custom refresh token."""

    token_type = 'refresh'
    lifetime = api_settings.REFRESH_TOKEN_LIFETIME
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
            if claim is None or claim in self.no_copy_claims:
                continue
            access[claim] = value

        return access

    @classmethod
    # See `CustomAccessToken.for_user` above for why this narrowing is suppressed rather than fixed.
    def for_user(cls, user: User) -> 'CustomRefreshToken':  # ty: ignore[invalid-method-override]
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
