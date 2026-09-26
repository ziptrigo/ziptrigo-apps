"""Public redirect for shortened QR code URLs."""

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

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

    # Increment scan count
    await qrcode.aincrement_scan_count()

    # Redirect to original URL
    if qrcode.original_url:
        return redirect(qrcode.original_url)

    return HttpResponse('No redirect URL available for this QR code', status=400)
