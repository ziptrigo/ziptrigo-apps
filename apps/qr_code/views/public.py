"""The shared QR code pages (issue #5): `/q/<code>` shows a QR code to anyone who has the link and
`/q/<code>/image` is its PNG (`?download=1` makes it an attachment). Read-only, no login.

**The code is the capability**, so every answer is `Cache-Control: no-store, private`,
`Referrer-Policy: strict-origin` and `X-Robots-Tag: noindex, nofollow` (and the page carries a
`noindex` meta), as the shared note pages are (`strict-origin`, not `no-referrer`: with
`no-referrer` a browser sends `Origin: null` on a same-origin form POST, which Django's CSRF check
refuses, and the report form to come is such a POST). **Every link that doesn't work** (an unknown
code, a revoked or expired link, a deleted QR code) renders the same standalone
`qr_code/unavailable.html` with status 404: it never says why. These paths are in
`TIMEZONE_MIDDLEWARE_SKIP_PREFIXES`, so the terms gate leaves them alone and they render in UTC and
the public date wording; the visitor may have no account at all.

`QR_SHARE_VIEW_IP` is charged before the lookup on both URLs (made-up codes spend it too); over it,
a plain 429 page. The image is the one the owner has, so a tracked code still encodes `/go/<code>`
and a scan of a shared copy counts in `scan_count` as usual.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.text import slugify
from django.views.decorators.http import require_GET

from apps.core import ratelimit

from ..models import QRCodeType, QRShareLink
from ..services import QRCodeGenerator, share


def private(response: HttpResponse) -> HttpResponse:
    """Mark an answer about one shared QR code as not cacheable, not to be sent as a referrer
    beyond the site's origin, and not for search engines: the code in the address is the
    capability."""
    response['Cache-Control'] = 'no-store, private'
    response['Referrer-Policy'] = 'strict-origin'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def unavailable(request: HttpRequest) -> HttpResponse:
    """The one answer for every link that doesn't work, whatever the reason: a neutral 404 that
    never says why. Rendered from no context at all (standalone), so a made-up code costs no
    session or user query and the bodies of every case are byte for byte the same."""
    return private(HttpResponse(render_to_string('qr_code/unavailable.html'), status=404))


def _view_limited(request: HttpRequest) -> HttpResponse | None:
    """The plain 429 page when this IP is over `QR_SHARE_VIEW_IP` (charged before the lookup)."""
    result = ratelimit.hit_ip(request, 'QR_SHARE_VIEW_IP')
    if result.allowed:
        return None
    return private(ratelimit.page_response(request, result))


def _link_for(code: str) -> QRShareLink | None:
    """The link that works now (`share.get_available`)."""
    return share.get_available(code)


@require_GET
def shared_page(request: HttpRequest, code: str) -> HttpResponse:
    if (response := _view_limited(request)) is not None:
        return response
    link = _link_for(code)
    if link is None:
        return unavailable(request)
    qrcode = link.qrcode
    is_url = qrcode.qr_type == QRCodeType.URL
    context = {
        'qrcode': qrcode,
        'link': link,
        'is_url': is_url,
        # Where a URL code leads: the destination a tracked code redirects to, else its content.
        'shown_text': (qrcode.original_url or qrcode.content) if is_url else qrcode.content,
        'page_url': share.link_url(link),
    }
    return private(render(request, 'qr_code/shared.html', context))


@require_GET
def shared_image(request: HttpRequest, code: str) -> HttpResponse:
    """The PNG, rendered on demand from the QR code's own settings. With `?download=1` an
    attachment named after the code."""
    if (response := _view_limited(request)) is not None:
        return response
    link = _link_for(code)
    if link is None:
        return unavailable(request)
    qrcode = link.qrcode
    response = HttpResponse(QRCodeGenerator.render_png(qrcode), content_type='image/png')
    response['X-Content-Type-Options'] = 'nosniff'
    if request.GET.get('download'):
        response['Content-Disposition'] = (
            f'attachment; filename="{slugify(qrcode.name) or "qr-code"}.png"'
        )
    return private(response)
