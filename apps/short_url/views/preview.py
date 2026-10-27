"""The preview page (`/s/<code>+`): where a link goes, shown without following it, so a recipient
can look before they click.

A password-protected link keeps its destination (and its title, which could give it away) back
until the visitor has entered the password in this session. It also hosts the "report this link"
form (`views.report`), which works for a protected link without unlocking it.
"""

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

from apps.core import ratelimit

from ..forms import ReportForm
from ..models import ShortLink
from ..services import destination_host, is_available, passwords
from .public import PublicHttpRequest, find_link, private, unavailable


def render_preview(
    request: PublicHttpRequest,
    link: ShortLink,
    *,
    report_form: ReportForm | None = None,
    reported: bool = False,
    status: int = 200,
) -> HttpResponse:
    """The preview page of an available link, with the report box in the given state."""
    locked = bool(link.password_hash) and not passwords.is_unlocked(request.session, link)
    response = render(
        request,
        'short_url/preview.html',
        {
            'link': link,
            'locked': locked,
            'host': None if locked else destination_host(link.destination),
            'report_form': report_form or ReportForm(),
            'reported': reported,
            'report_open': reported or report_form is not None,
        },
        status=status,
    )
    return private(response)


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

    return render_preview(request, link)
