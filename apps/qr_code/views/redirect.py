"""Public redirect for shortened QR code URLs."""

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from apps.core import ratelimit

from ..models import QRCode


async def redirect_short_url(request: HttpRequest, short_code: str) -> HttpResponse:
    """Redirect a scanned short link (`/go/<short_code>`) to the QR code's original URL."""
    try:
        qrcode = await sync_to_async(QRCode.objects.get)(short_code=short_code)
    except QRCode.DoesNotExist:
        return HttpResponse('QR Code not found', status=404)

    # Redirect to dashboard if QR code is soft-deleted
    if qrcode.deleted_at:
        return redirect('qr_code:dashboard')

    # Per-IP limit (issue #53), deliberately generous -- a physical QR code is meant to get
    # scanned a lot. Over the limit, still redirect (a real visitor scanning a physical code must
    # never see an error just because other people behind the same IP/NAT scanned it too) but skip
    # the scan-count write, which is what actually costs a DB write per hit; the redirect itself
    # is stateless. Kinder to shared-IP visitors than a 429; documented in CLAUDE.md.
    limited = await ratelimit.ahit_ip(request, 'QR_REDIRECT_IP')
    if limited.allowed:
        await qrcode.aincrement_scan_count()

    # Redirect to original URL
    if qrcode.original_url:
        return redirect(qrcode.original_url)

    return HttpResponse('No redirect URL available for this QR code', status=400)
