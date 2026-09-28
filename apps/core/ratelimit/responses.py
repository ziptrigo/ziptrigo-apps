"""429 responses for plain Django/HTMX views (session form views in `accounts`, `qr_code`,
`file_transfer`). Ninja endpoints don't use these -- they raise `exceptions.RateLimitExceeded`
instead and let `config/api.py`'s exception handler build the JSON response.

Every response here carries a `Retry-After` header (RFC 9110 15.5.30), and every response is
generic on purpose: it never says *which* limit was hit or names the resource, so it can't be
used to enumerate valid targets (an account, a transfer slug, ...) any more precisely than the
429 status itself already implies.
"""

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from apps.core.htmx import is_htmx

from .limiter import RateLimitResult

DEFAULT_MESSAGE = 'Too many requests. Please wait a moment and try again.'


def json_response(result: RateLimitResult, message: str = DEFAULT_MESSAGE) -> JsonResponse:
    """For the JSON-over-HTTP endpoints that aren't ninja (`file_transfer`'s upload coordination
    views), matching their existing `{'error': ...}` shape rather than ninja's `{'detail': ...}`.
    """
    response = JsonResponse({'error': message}, status=429)
    response['Retry-After'] = str(result.retry_after)
    return response


def page_response(
    request: HttpRequest,
    result: RateLimitResult,
    message: str = DEFAULT_MESSAGE,
    *,
    template: str = 'core/429.html',
) -> HttpResponse:
    """A full, friendly 429 page -- for a plain (non-htmx) request."""
    response = render(request, template, {'message': message}, status=429)
    response['Retry-After'] = str(result.retry_after)
    return response


def htmx_response(
    request: HttpRequest,
    result: RateLimitResult,
    message: str = DEFAULT_MESSAGE,
    *,
    retarget: str | None = None,
    template: str = 'core/partials/rate_limited.html',
) -> HttpResponse:
    """A small message partial -- for an htmx request. `core/base.html`'s `htmx-config` swaps 429
    like 422, so this actually renders; `retarget` sends it somewhere other than the request's own
    target (see `apps/qr_code/views/editor.py`'s `_errors` for the same pattern on 422)."""
    response = render(request, template, {'message': message}, status=429)
    response['Retry-After'] = str(result.retry_after)
    if retarget:
        response['HX-Retarget'] = retarget
        response['HX-Reswap'] = 'innerHTML'
    return response


def web_response(
    request: HttpRequest,
    result: RateLimitResult,
    message: str = DEFAULT_MESSAGE,
    *,
    retarget: str | None = None,
) -> HttpResponse:
    """`htmx_response` for an htmx request, `page_response` otherwise -- the usual choice for a
    session form view protected by a rate limit."""
    if is_htmx(request):
        return htmx_response(request, result, message, retarget=retarget)
    return page_response(request, result, message)
