from typing import Any
from uuid import UUID

from django.contrib.auth.backends import ModelBackend
from django.http import HttpRequest

from .models import User


class EmailBackend(ModelBackend):
    """Authenticate using email instead of username."""

    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> User | None:
        email = kwargs.get('email') or username
        if not email or not password:
            return None

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return None

        if user.check_password(password):
            return user
        return None

    def get_user(self, user_id: UUID | str) -> User | None:
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
