"""Form handlers for the QR code editor page (`qr_code/qrcode_editor.html`).

htmx posts the editor form here. Validation errors come back as the `form_errors` partial with
status 422, swapped into the page's message box; see `apps/core/htmx.py`.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import hx_redirect

from .. import services
from ..forms import QRCodeCreateForm, QRCodeRenameForm
from ..models import QRCode


def _errors(request: AuthenticatedHttpRequest, form) -> HttpResponse:
    response = render(request, 'qr_code/partials/form_errors.html', {'form': form}, status=422)
    # The preview button targets the image; errors always go to the message box.
    response['HX-Retarget'] = '#qrcode-msg'
    response['HX-Reswap'] = 'innerHTML'
    return response


@login_required
@require_POST
def qrcode_create_submit(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Create a QR code from the editor, then go to the dashboard."""
    form = QRCodeCreateForm(request.POST)
    if not form.is_valid():
        return _errors(request, form)

    services.create_qrcode(request.user, **form.qrcode_fields())
    return hx_redirect(request, reverse('dashboard'))


@login_required
@require_POST
def qrcode_edit_submit(request: AuthenticatedHttpRequest, qr_id: str) -> HttpResponse:
    """Rename a QR code from the editor, then go to the dashboard."""
    try:
        qrcode = QRCode.objects.get(id=qr_id, created_by=request.user, deleted_at__isnull=True)
    except QRCode.DoesNotExist:
        raise Http404('QR Code not found')

    form = QRCodeRenameForm(request.POST, instance=qrcode)
    if not form.is_valid():
        return _errors(request, form)

    form.save()
    return hx_redirect(request, reverse('dashboard'))


@login_required
@require_POST
def qrcode_preview(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render a preview of the QR code in the editor, without saving it."""
    form = QRCodeCreateForm(request.POST)
    if not form.is_valid():
        return _errors(request, form)

    image_url = services.render_preview(request.user, **form.qrcode_fields())
    return render(request, 'qr_code/partials/preview_image.html', {'image_url': image_url})
