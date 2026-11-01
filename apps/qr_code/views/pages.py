from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AnonymousUser
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.text import slugify

from apps.accounts.http import MaybeAuthenticatedHttpRequest

from ..models import QRCode
from ..services import QRCodeGenerator


@login_required
def dashboard(request: MaybeAuthenticatedHttpRequest) -> HttpResponse:
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
        'sort': 'name' if sort == 'name' else 'newest',
        'sort_options': [('newest', 'Newest'), ('name', 'Name')],
    }
    return render(request, 'qr_code/dashboard.html', context)


@login_required
def qrcode_editor(request: MaybeAuthenticatedHttpRequest, qr_id: str | None = None) -> HttpResponse:
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
            qrcode = QRCode.objects.get(id=qr_id, created_by=user, deleted_at__isnull=True)
        except QRCode.DoesNotExist:
            # 404 if the QR code doesn't exist, isn't the user's or was deleted
            from django.http import Http404

            raise Http404('QR Code not found')

    context = {'qrcode': qrcode, 'prefill': {}}
    return render(request, 'qr_code/qrcode_editor.html', context)


@login_required
def qrcode_duplicate(request: MaybeAuthenticatedHttpRequest, qr_id: str) -> HttpResponse:
    """Render the QR code editor in create mode, pre-filled from an existing QR code."""
    user = request.user

    # Narrow type for static checkers; guarded by @login_required.
    if isinstance(user, AnonymousUser):
        raise RuntimeError('Authenticated user required')

    try:
        source = QRCode.objects.get(id=qr_id, created_by=user, deleted_at__isnull=True)
    except QRCode.DoesNotExist:
        from django.http import Http404

        raise Http404('QR Code not found')

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
    return render(request, 'qr_code/qrcode_editor.html', context)


@login_required
def qrcode_image(request: MaybeAuthenticatedHttpRequest, qr_id: str) -> HttpResponse:
    """Serve one of the user's QR codes as a PNG, rendered from its saved settings.

    With `?download=1` the response is an attachment named after the code, so the browser saves it
    instead of showing it (the dashboard's "Download" menu item).
    """
    try:
        qrcode = QRCode.objects.get(id=qr_id, created_by=request.user, deleted_at__isnull=True)
    except QRCode.DoesNotExist:
        raise Http404('QR Code not found')

    response = HttpResponse(QRCodeGenerator.render_png(qrcode), content_type='image/png')
    response['Cache-Control'] = 'private, max-age=300'
    if request.GET.get('download'):
        response['Content-Disposition'] = (
            f'attachment; filename="{slugify(qrcode.name) or "qr-code"}.png"'
        )
    return response
