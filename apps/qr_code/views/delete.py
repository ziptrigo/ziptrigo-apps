from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_POST

from apps.accounts.http import AuthenticatedHttpRequest

from ..models import QRCode


@login_required
@require_POST
def qrcode_delete(request: AuthenticatedHttpRequest, qr_id: str) -> HttpResponse:
    """Soft-delete one of the user's QR codes (from the dashboard)."""
    try:
        qrcode = QRCode.objects.get(id=qr_id, created_by=request.user, deleted_at__isnull=True)
    except QRCode.DoesNotExist:
        raise Http404('QR Code not found')

    qrcode.soft_delete()
    return HttpResponse(status=204)
