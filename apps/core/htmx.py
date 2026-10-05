"""Helpers for views that answer HTMX requests.

Convention for form views: on success, redirect (`hx_redirect`) or return the updated partial;
on validation errors, re-render the partial with status 422, which `core/base.html` configures
htmx to swap in like a success.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def is_htmx(request: HttpRequest) -> bool:
    """Whether the request was made by htmx."""
    return request.headers.get('HX-Request') == 'true'


def hx_redirect(request: HttpRequest, url: str) -> HttpResponse:
    """Navigate to ``url``: a full-page redirect for htmx requests, a plain 302 otherwise.

    Only same-host URLs are followed; anything else falls back to the site root, so a
    user-supplied value can never turn this into an open redirect.
    """
    if not url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        url = '/'
    if is_htmx(request):
        response = HttpResponse()
        response['HX-Redirect'] = url
        return response
    return redirect(url)
