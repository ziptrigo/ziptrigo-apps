"""Typed `HttpRequest` for `core`'s own logged-in views."""

from django.contrib.auth.models import PermissionsMixin
from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest


class AuthenticatedHttpRequest(HttpRequest):
    """`HttpRequest` typed with the `.user` attribute `AuthenticationMiddleware` adds at runtime,
    for views behind `@login_required` (so `.user` is never anonymous in the body).

    `apps.accounts.http.AuthenticatedHttpRequest` is the same idea with the concrete `User`, but
    `core` sits below `accounts` and must not import it, so the user is typed `PermissionsMixin`
    here (the same compromise `apps.core.admin_site` makes for the admin).
    """

    user: PermissionsMixin
    session: SessionBase
