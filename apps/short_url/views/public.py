"""What the public pages (the redirect, the password page, the preview) share: the neutral
"unavailable" answer and the case-insensitive lookup by code."""

from django.contrib.sessions.backends.base import SessionBase
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ..models import ShortLink
from ..services import normalize_key


class PublicHttpRequest(HttpRequest):
    """`HttpRequest` typed with the `.session` attribute `SessionMiddleware` adds at runtime (a
    visitor to a public page has a session, but no login)."""

    session: SessionBase


def unavailable(request: HttpRequest) -> HttpResponse:
    """The one answer for every link that doesn't work, whatever the reason (unknown, disabled,
    expired, past its click limit, deleted, taken down, held): a neutral 404 that never says why,
    like file transfer's download page."""
    response = render(request, 'short_url/unavailable.html', status=404)
    response['Cache-Control'] = 'no-store, private'
    return response


def find_link(code: str) -> ShortLink | None:
    """The link with this code, whatever its state, or None. Case-insensitive: the key is the
    lower-cased code."""
    return ShortLink.objects.filter(code_key=normalize_key(code)).first()


def private(response: HttpResponse) -> HttpResponse:
    """Mark a public page that is about one link as not cacheable and not for search engines."""
    response['Cache-Control'] = 'no-store, private'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response
