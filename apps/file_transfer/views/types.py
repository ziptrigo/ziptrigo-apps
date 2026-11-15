"""Request types of the public (anonymous-friendly) views."""

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest

from apps.accounts.models import User


class PublicHttpRequest(HttpRequest):
    """`HttpRequest` typed with `.session`, added by `SessionMiddleware` for every request --
    unlike `apps.accounts.http.AuthenticatedHttpRequest`, this doesn't require login: these are
    the public, unauthenticated download-page views. `.user` is only read by a file request's link
    (issue #306), whose audience may need a signed-in account."""

    session: SessionBase
    user: User | AnonymousUser
