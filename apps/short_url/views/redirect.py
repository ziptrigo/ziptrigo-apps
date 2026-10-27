"""The public short link: `/s/<code>` redirects, `/s/<code>+` shows where it goes."""

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_safe

from apps.core import ratelimit

from ..models import ShortLink
from ..services import is_available, normalize_key, record_click


def unavailable(request: HttpRequest) -> HttpResponse:
    """The one answer for every link that doesn't work, whatever the reason (unknown, disabled,
    expired, past its click limit, deleted, taken down, held): a neutral 404 that never says why,
    like file transfer's download page."""
    response = render(request, 'short_url/unavailable.html', status=404)
    response['Cache-Control'] = 'no-store, private'
    return response


@require_safe
def redirect_link(request: HttpRequest, code: str, preview: str | None = None) -> HttpResponse:
    """Follow a short link (`/s/<code>`), or look at it first (`/s/<code>+`)."""
    # Per-IP limit, deliberately generous, charged *before* the lookup so requests for made-up
    # codes spend the same budget as real ones. Over the limit nothing becomes an error: a known
    # link still redirects (a visitor behind a busy shared IP must never see an error because
    # others followed the same link) but its click is not recorded, and an unknown code is a plain
    # 404, never a 429. Same shape as the `/go/<code>` redirect (`apps/qr_code/views/redirect.py`).
    # One exception: a link with a click cap is always counted, because there the counter is what
    # ends the link, not just a statistic.
    limited = ratelimit.hit_ip(request, 'SU_REDIRECT_IP')

    # Case-insensitive: the key is the lower-cased code.
    link = ShortLink.objects.filter(code_key=normalize_key(code)).first()
    if link is None or not is_available(link):
        return unavailable(request)

    if preview:
        # Phase 1 placeholder: the real preview page (with its report form) is a later phase.
        response = render(request, 'short_url/preview.html', {'link': link})
        response['Cache-Control'] = 'no-store, private'
        return response

    # Only a GET is a click: a HEAD (a link checker, a scanner) redirects without recording.
    if request.method == 'GET' and (limited.allowed or link.max_clicks is not None):
        if not record_click(link, request):
            return unavailable(request)

    # 302, never 301: the destination can be edited, and a browser must not cache the old one.
    response = HttpResponseRedirect(link.destination)
    response['Cache-Control'] = 'no-store, private'
    return response
