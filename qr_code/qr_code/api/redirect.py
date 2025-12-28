"""Async redirect endpoint for shortened URLs."""

from asgiref.sync import sync_to_async
from django.http import HttpResponse
from django.shortcuts import redirect
from ninja import Router
from src.qr_code.models import QRCode

router = Router()


@router.get('/{short_code}', auth=None)
async def redirect_short_url(request, short_code: str):
    """Redirect endpoint for shortened URLs (public access)."""
    try:
        qrcode = await sync_to_async(QRCode.objects.get)(short_code=short_code)
    except QRCode.DoesNotExist:
        return HttpResponse('QR Code not found', status=404)

    # Redirect to dashboard if QR code is soft-deleted
    if qrcode.deleted_at:
        return redirect('dashboard')

    # Increment scan count
    await qrcode.aincrement_scan_count()

    # Redirect to original URL
    if qrcode.original_url:
        return redirect(qrcode.original_url)

    return HttpResponse('No redirect URL available for this QR code', status=400)
