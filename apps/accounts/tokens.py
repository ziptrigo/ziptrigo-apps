from datetime import timedelta
from typing import cast

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
        # `Token.for_user` is itself mistyped upstream as `cls: T` instead of `cls: type[T]`, so ty
        # sees `cls` (a class object) checked against a bound of `Token` (an instance) and always
        # rejects it -- nothing on this side to fix.
        token = super().for_user(user)  # ty: ignore[invalid-argument-type]
        token['email'] = user.email
        # `Token.for_user` is typed as returning `Self`, but `Self` on the base class resolves to
        # `Token`, not this subclass -- ty has no way to know the actual runtime type follows
        # `cls`, so it's cast rather than fixed.
        return cast(CustomAccessToken, token)


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
            if claim in self.no_copy_claims:
                continue
            # `self.payload` is typed as `dict[str, Any]`, but ty widens `claim` to
            # `str | None` when iterating `.items()` here -- JWT claim keys are always `str` at
            # runtime, so this is a cast rather than a real `None`-guard (see the `for_user`
            # cast below for the same kind of ty/runtime gap).
            access[cast(str, claim)] = value

        return access

    @classmethod
    # See `CustomAccessToken.for_user` above for why this narrowing is suppressed rather than fixed.
    def for_user(cls, user: User) -> 'CustomRefreshToken':  # ty: ignore[invalid-method-override]
        """Create a refresh token for the given user."""
        # See `CustomAccessToken.for_user` above for why this is suppressed rather than fixed.
        token = super().for_user(user)  # ty: ignore[invalid-argument-type]
        token['email'] = user.email

        # See `CustomAccessToken.for_user` above for why this is a cast, not a bug.
        return cast(CustomRefreshToken, token)


class PasswordResetToken(Token):
    """JWT token for password reset links."""

    token_type = 'password_reset'
    lifetime = timedelta(hours=settings.PASSWORD_RESET_TOKEN_TTL_HOURS)
