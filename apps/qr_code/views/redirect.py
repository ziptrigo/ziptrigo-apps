"""Public redirect for shortened QR code URLs."""

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.core import ratelimit

from ..models import QRCode


async def redirect_short_url(request: HttpRequest, short_code: str) -> HttpResponse:
    """Redirect a scanned short link (`/go/<short_code>`) to the QR code's original URL."""
    # Per-IP limit (issue #53), deliberately generous -- a physical QR code is meant to get
    # scanned a lot. Charged before the lookup (issue #119), so requests for made-up codes spend
    # the same budget as scans of real ones. Over the limit nothing becomes an error: a known code
    # still redirects (a real visitor scanning a physical code must never see an error just
    # because other people behind the same IP/NAT scanned it too) but its scan-count write is
    # skipped, and an unknown code is still a plain 404, never a 429. The rule isn't here to
    # shed load (the redirect itself is stateless, and an `UPDATE ... SET scan_count = scan_count
    # + 1` is already cheap, more so now that it's the same kind of atomic upsert
    # `apps.core.ratelimit`'s own DB storage path uses; a request for an unknown code still costs
    # its lookup) -- it's here so `QRCode.scan_count` stays a meaningful count of *distinct*
    # scans rather than inflating under a burst from one shared IP/NAT. Kinder to shared-IP
    # visitors than a 429; documented in CLAUDE.md. Shedding a flood of unknown codes is nginx's
    # job (the looser `/go/` zone in the infra repo's `apps/ziptrigo-apps/nginx.conf`).
    limited = await ratelimit.ahit_ip(request, 'QR_REDIRECT_IP')

    try:
        qrcode = await sync_to_async(QRCode.objects.get)(short_code=short_code)
    except QRCode.DoesNotExist:
        return await _unavailable(request)

    # A deleted code answers like an unknown one: the person scanning it is not its owner, so
    # sending them to the (login-only) dashboard only led to the login page.
    if qrcode.deleted_at:
        return await _unavailable(request)

    if limited.allowed:
        await qrcode.aincrement_scan_count()

    # Redirect to original URL
    if qrcode.original_url:
        return redirect(qrcode.original_url)

    return HttpResponse('No redirect URL available for this QR code', status=400)


async def _unavailable(request: HttpRequest) -> HttpResponse:
    """The neutral 404 page for an unknown or deleted code. Rendered in a thread: the site layout
    reads the session (`request.user`), which is a database query."""
    return await sync_to_async(render)(request, 'qr_code/unavailable.html', status=404)
