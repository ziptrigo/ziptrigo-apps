from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ..models import QRCode


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Render the user dashboard with their QR codes."""
    user = request.user

    # Narrow type for static checkers; guarded by @login_required.
    if isinstance(user, AnonymousUser):
        raise RuntimeError('Authenticated user required')

    query = request.GET.get('q', '')
    sort = request.GET.get('sort', '')

    qrcodes = QRCode.objects.filter(created_by=user, deleted_at__isnull=True)

    if query:
        qrcodes = qrcodes.filter(name__icontains=query)

    if sort == 'name':
        qrcodes = qrcodes.order_by('name')
    else:
        qrcodes = qrcodes.order_by('-created_at')

    context = {
        'qrcodes': qrcodes,
        'query': query,
    }
    return render(request, 'dashboard.html', context)


@login_required
def qrcode_editor(request: HttpRequest, qr_id: str | None = None) -> HttpResponse:
    """Render the QR code editor page for creating or editing QR codes.

    Args:
        request: The HTTP request object.
        qr_id: Optional UUID of the QR code to edit. If None, create mode.
    """
    user = request.user

    # Narrow type for static checkers; guarded by @login_required.
    if isinstance(user, AnonymousUser):
        raise RuntimeError('Authenticated user required')

    qrcode = None
    if qr_id:
        # Edit mode: fetch the QR code and validate ownership
        try:
            qrcode = QRCode.objects.get(id=qr_id, created_by=user)
        except QRCode.DoesNotExist:
            # Return 404 if QR code doesn't exist or doesn't belong to the user
            from django.http import Http404
            raise Http404("QR Code not found")

    context = {'qrcode': qrcode, 'prefill': {}}
    return render(request, 'qrcode_editor.html', context)


@login_required
def qrcode_duplicate(request: HttpRequest, qr_id: str) -> HttpResponse:
    """Render the QR code editor in create mode, pre-filled from an existing QR code."""
    user = request.user

    # Narrow type for static checkers; guarded by @login_required.
    if isinstance(user, AnonymousUser):
        raise RuntimeError('Authenticated user required')

    try:
        source = QRCode.objects.get(id=qr_id, created_by=user)
    except QRCode.DoesNotExist:
        from django.http import Http404
        raise Http404("QR Code not found")

    # Prefer the original URL (if present) for URL-type QR codes; otherwise use content.
    if source.qr_type == 'url' and source.original_url:
        prefill_content = source.original_url
    else:
        prefill_content = source.content

    prefill = {
        'name': source.name,
        'qr_type': source.qr_type,
        'qr_format': source.qr_format,
        'content': prefill_content,
        'use_url_shortening': bool(source.use_url_shortening and source.qr_type == 'url'),
    }

    context = {
        'qrcode': None,
        'prefill': prefill,
    }
    return render(request, 'qrcode_editor.html', context)
