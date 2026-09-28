"""Authentication classes for the `/api/` endpoints of every app."""

from django.http import HttpRequest
from ninja_jwt.authentication import AsyncJWTAuth as BaseAsyncJWTAuth
from ninja_jwt.authentication import JWTAuth as BaseJWTAuth
from ninja_jwt.exceptions import AuthenticationFailed

from .models import User


class JWTAuth(BaseJWTAuth):
    """JWT-based authentication for users with status check.

    `ninja_jwt`'s `authenticate` raises `InvalidToken`/`AuthenticationFailed` (e.g. a malformed,
    expired or unrecognised token) rather than returning `None`. Django Ninja's auth contract is
    the reverse: an auth callable signals "not authenticated" by returning `None`, which
    `Operation._run_authentication` turns into a 401; any exception it doesn't otherwise recognise
    falls through to the generic handler, which is a 500 in production. Catching it here restores
    the documented "bad token -> 401" contract instead of a 500 on every malformed Authorization
    header.
    """

    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        try:
            user: User | None = super().authenticate(request, token)
        except AuthenticationFailed:
            return None

        if user is None:
            return None

        if user.status != User.STATUS_ACTIVE:
            return None

        return user


class AsyncJWTAuth(BaseAsyncJWTAuth):
    """`JWTAuth` for async endpoints."""

    async def authenticate(self, request: HttpRequest, token: str) -> User | None:
        try:
            user: User | None = await super().authenticate(request, token)
        except AuthenticationFailed:
            return None

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
