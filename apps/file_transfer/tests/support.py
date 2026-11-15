"""Small typed helpers of the file request tests."""

from typing import cast

from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.signed_cookies import SessionStore

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory


def new_user(**fields) -> User:
    """A saved `User` (the factory's own return type is its class, not the model)."""
    return cast(User, UserFactory(**fields))


def new_session() -> SessionBase:
    """A real session that needs no database (a signed cookie)."""
    return SessionStore()
