"""Typed `HttpRequest`s for views, shared by every app that serves logged-in pages."""

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest

from .models import User


class AuthenticatedHttpRequest(HttpRequest):
    """`HttpRequest` typed with the `.user` attribute `AuthenticationMiddleware` adds at runtime.

    `HttpRequest` itself doesn't declare `.user` -- `ty` has no equivalent of django-stubs' plugin
    to know middleware adds it. Narrowed to the concrete `User` (not unioned with `AnonymousUser`)
    because `@login_required` redirects anonymous requests before the view body runs. `session`
    (from `SessionMiddleware`) is declared for the same reason.
    """

    user: User
    session: SessionBase


class MaybeAuthenticatedHttpRequest(HttpRequest):
    """Same as `AuthenticatedHttpRequest`, but for views that check `.is_authenticated` themselves
    instead of relying on `@login_required`, so `.user` may still be `AnonymousUser`.
    """

    user: User | AnonymousUser
