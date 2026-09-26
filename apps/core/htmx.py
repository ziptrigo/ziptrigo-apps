"""Helpers for views that answer HTMX requests.

Convention for form views: on success, redirect (`hx_redirect`) or return the updated partial;
on validation errors, re-render the partial with status 422, which `core/base.html` configures
htmx to swap in like a success.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect


def is_htmx(request: HttpRequest) -> bool:
    """Whether the request was made by htmx."""
    return request.headers.get('HX-Request') == 'true'


def hx_redirect(request: HttpRequest, url: str) -> HttpResponse:
    """Navigate to ``url``: a full-page redirect for htmx requests, a plain 302 otherwise."""
    if is_htmx(request):
        response = HttpResponse()
        response['HX-Redirect'] = url
        return response
    return redirect(url)
