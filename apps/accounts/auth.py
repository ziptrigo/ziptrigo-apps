"""Authentication classes for the `/api/` endpoints of every app."""

from django.http import HttpRequest
from ninja_jwt.authentication import AsyncJWTAuth as BaseAsyncJWTAuth
from ninja_jwt.authentication import JWTAuth as BaseJWTAuth

from .models import User


class JWTAuth(BaseJWTAuth):
    """JWT-based authentication for users with status check."""

    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        user: User | None = super().authenticate(request, token)

        if user is None:
            return None

        if user.status != User.STATUS_ACTIVE:
            return None

        return user


class AsyncJWTAuth(BaseAsyncJWTAuth):
    """`JWTAuth` for async endpoints."""

    async def authenticate(self, request: HttpRequest, token: str) -> User | None:
        user: User | None = await super().authenticate(request, token)

        if user is None:
            return None

        if user.status != User.STATUS_ACTIVE:
            return None

        return user


class AdminAuth(JWTAuth):
    """JWT authentication that also requires admin (staff) privileges."""

    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        user: User | None = super().authenticate(request, token)

        if user is None:
            return None

        if not user.is_staff:
            return None

        return user
