"""The preview page (`/s/<code>+`): where a link goes, shown without following it, so a recipient
can look before they click.

A password-protected link keeps its destination (and its title, which could give it away) back
until the visitor has entered the password in this session. This is where the report form will
go in the abuse-tooling phase.
"""

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

from apps.core import ratelimit

from ..services import destination_host, is_available, passwords
from .public import PublicHttpRequest, find_link, private, unavailable


@require_safe
def preview_link(request: PublicHttpRequest, code: str) -> HttpResponse:
    # Its own per-IP rule, charged before the lookup like the redirect's. Unlike the redirect,
    # this is a page nobody follows from a printed code, so over the limit it is a plain 429.
    limited = ratelimit.hit_ip(request, 'SU_PREVIEW_IP')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)

    link = find_link(code)
    if link is None or not is_available(link):
        return unavailable(request)

    locked = bool(link.password_hash) and not passwords.is_unlocked(request.session, link)
    response = render(
        request,
        'short_url/preview.html',
        {
            'link': link,
            'locked': locked,
            'host': None if locked else destination_host(link.destination),
        },
    )
    return private(response)
