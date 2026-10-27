"""The public short link: `/s/<code>` redirects, `/s/<code>+` shows where it goes first."""

from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_safe

from apps.core import ratelimit

from ..services import clicks, is_available, passwords
from .preview import preview_link
from .public import PublicHttpRequest, find_link, unavailable


@require_safe
def redirect_link(
    request: PublicHttpRequest, code: str, preview: str | None = None
) -> HttpResponse:
    """Follow a short link (`/s/<code>`), or look at it first (`/s/<code>+`)."""
    if preview:
        return preview_link(request, code)

    # Per-IP limit, deliberately generous, charged *before* the lookup so requests for made-up
    # codes spend the same budget as real ones. Over the limit nothing becomes an error: a known
    # link still redirects (a visitor behind a busy shared IP must never see an error because
    # others followed the same link) but its click event is not recorded, and an unknown code is a
    # plain 404, never a 429. Same shape as the `/go/<code>` redirect
    # (`apps/qr_code/views/redirect.py`). One exception: a link with a click cap is always
    # counted (its counter, without the event), because there the counter is what ends the link,
    # not just a statistic.
    limited = ratelimit.hit_ip(request, 'SU_REDIRECT_IP')

    link = find_link(code)
    if link is None or not is_available(link):
        return unavailable(request)

    if link.password_hash and not passwords.is_unlocked(request.session, link):
        # Asked for the password first; nothing about the destination is sent. Not counted: a
        # click is the redirect that follows.
        response = HttpResponseRedirect(reverse('s:unlock', args=[link.code]))
        response['Cache-Control'] = 'no-store, private'
        return response

    # Only a GET is a click: a HEAD (a link checker, a scanner) redirects without recording.
    if request.method == 'GET' and (limited.allowed or link.max_clicks is not None):
        if not clicks.record(link, request, with_event=limited.allowed):
            return unavailable(request)

    # 302, never 301: the destination can be edited, and a browser must not cache the old one.
    response = HttpResponseRedirect(link.destination)
    response['Cache-Control'] = 'no-store, private'
    return response
