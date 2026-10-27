"""A link's own page (`/links/<id>/`) and its QR image (`/links/<id>/qr.png`)."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.services.qr_image import render_png

from ..models import LinkStatus
from ..services import display_state, is_destination_blocked, quote_enable, short_url
from .common import owned_link, render_page


@login_required
@require_GET
@ensure_csrf_cookie
def link_detail(request: AuthenticatedHttpRequest, link_id: str) -> HttpResponse:
    link = owned_link(request, link_id)
    link.enable_cost = quote_enable(link).total
    # A database query, so only asked of a link that would otherwise be working.
    blocked = link.status == LinkStatus.ACTIVE and is_destination_blocked(link)
    return render_page(
        request,
        'short_url/detail.html',
        {
            'link': link,
            'short_url': short_url(link.code),
            'state': display_state(link, now=timezone.now(), destination_blocked=blocked),
            # Held for review or taken down: the owner can change nothing (a held link can still
            # be disabled).
            'frozen': link.status == LinkStatus.TAKEN_DOWN or link.held_for_review_at is not None,
        },
    )


@login_required
@require_GET
def link_qr(request: AuthenticatedHttpRequest, link_id: str) -> HttpResponse:
    """The QR code of the short link as a PNG (`?download=1` to save it as `<code>.png`).
    Rendered on demand, never stored; the QR product is not involved."""
    link = owned_link(request, link_id)
    limited = ratelimit.hit_user(request.user, 'SU_QR_USER')
    if not limited.allowed:
        return ratelimit.page_response(request, limited)
    response = HttpResponse(render_png(short_url(link.code)), content_type='image/png')
    # What it encodes can't change (a code is permanent), so the browser may keep it a while.
    response['Cache-Control'] = 'private, max-age=3600'
    if request.GET.get('download'):
        response['Content-Disposition'] = f'attachment; filename="{link.code}.png"'
    return response
