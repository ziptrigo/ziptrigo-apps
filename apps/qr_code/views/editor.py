"""Form handlers for the QR code editor page (`qr_code/qrcode_editor.html`).

htmx posts the editor form here. Validation errors come back as the `form_errors` partial with
status 422, swapped into the page's message box; see `apps/core/htmx.py`.

Short codes for tracked QR codes are issued by the server (`qrcode_short_code`) and kept in the
session until the QR code is saved, so users can't pick their own.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import hx_redirect

from .. import services
from ..forms import QRCodeCreateForm, QRCodeRenameForm
from ..models import QRCode

SHORT_CODE_SESSION_KEY = 'qr_code.short_code'


def _errors(request: AuthenticatedHttpRequest, form) -> HttpResponse:
    response = render(request, 'qr_code/partials/form_errors.html', {'form': form}, status=422)
    # The preview button targets the image; errors always go to the message box.
    response['HX-Retarget'] = '#qrcode-msg'
    response['HX-Reswap'] = 'innerHTML'
    return response


@login_required
@require_GET
def qrcode_short_code(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Issue the short code the editor shows (and will save) for a tracked QR code.

    Reuses the code already in the session while it's still free, so toggling the checkbox
    doesn't change the link.
    """
    short_code = request.session.get(SHORT_CODE_SESSION_KEY)
    if not short_code or not services.is_short_code_available(short_code):
        short_code = services.new_short_code()
        request.session[SHORT_CODE_SESSION_KEY] = short_code

    context = {'short_url': services.short_url(short_code)}
    return render(request, 'qr_code/partials/short_url.html', context)


@login_required
@require_POST
def qrcode_create_submit(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Create a QR code from the editor, then go to the dashboard."""
    form = QRCodeCreateForm(request.POST)
    if not form.is_valid():
        return _errors(request, form)

    services.create_qrcode(
        request.user,
        short_code=request.session.pop(SHORT_CODE_SESSION_KEY, None),
        **form.qrcode_fields(),
    )
    return hx_redirect(request, reverse('qr_code:dashboard'))


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
    return hx_redirect(request, reverse('qr_code:dashboard'))


@login_required
@require_POST
def qrcode_preview(request: AuthenticatedHttpRequest) -> HttpResponse:
    """Render a preview of the QR code in the editor, without saving anything."""
    form = QRCodeCreateForm(request.POST)
    if not form.is_valid():
        return _errors(request, form)

    image_url = services.render_preview(
        request.user,
        short_code=request.session.get(SHORT_CODE_SESSION_KEY),
        **form.qrcode_fields(),
    )
    return render(request, 'qr_code/partials/preview_image.html', {'image_url': image_url})
