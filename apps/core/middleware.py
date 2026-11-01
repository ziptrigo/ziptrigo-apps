"""Middleware for the site shell."""

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import resolve_url
from django.utils.http import url_has_allowed_host_and_scheme

from .htmx import is_htmx


class HtmxLoginRedirectMiddleware:
    """Turn the login redirect of an htmx request into a full-page navigation (issue #152).

    `@login_required` answers a request without a session with a 302 to the login page. htmx's XHR
    follows it silently and swaps the whole login page (a second header and footer) into whatever
    element the request targeted, dropping what the user typed. For an htmx request this answers
    `HX-Redirect` instead, so the browser goes to the login page, and the `next` it carries is the
    page the user was on (`HX-Current-URL`), not the partial endpoint that was posted to, which
    would render a fragment after sign-in. Views that answer an anonymous htmx request on their own
    (the notification bell's 204) never reach this: only a redirect to `LOGIN_URL` is rewritten.
    """

    async_capable = True
    sync_capable = True

    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponse | Awaitable[HttpResponse]]
    ):
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(self, request: HttpRequest):
        if iscoroutinefunction(self):
            return self.__acall__(request)
        response = self.get_response(request)
        return _rewrite(request, response)  # ty: ignore[invalid-argument-type]

    async def __acall__(self, request: HttpRequest) -> HttpResponse:
        response = await self.get_response(request)  # ty: ignore[invalid-await]
        return _rewrite(request, response)


def _rewrite(request: HttpRequest, response: HttpResponse) -> HttpResponse:
    if response.status_code != 302 or not is_htmx(request):
        return response
    login_path = resolve_url(settings.LOGIN_URL)
    if urlsplit(response.get('Location', '')).path != login_path:
        return response
    redirect = redirect_to_login(_current_page(request))
    answer = HttpResponse()
    answer['HX-Redirect'] = redirect['Location']
    return answer


def _current_page(request: HttpRequest) -> str:
    """The same-site path of the page htmx was on, else the site root."""
    current = request.headers.get('HX-Current-URL', '')
    if current and url_has_allowed_host_and_scheme(
        current, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        parts = urlsplit(current)
        return parts.path + (f'?{parts.query}' if parts.query else '')
    return '/'
