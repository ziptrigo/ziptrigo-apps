"""The upload page and the JSON endpoints behind it.

The browser sends each file straight to S3 with a presigned PUT and only coordinates with Django
over JSON (so, like `file_transfer`'s upload views, these answer JSON rather than HTMX partials):

1. `POST /receipts/upload/start/` with the file's name, size, type and base64 SHA-256 -> a
   presigned URL plus the headers the signature binds (`services.uploads.start_upload`).
2. The browser PUTs the file to that URL.
3. `POST /receipts/upload/<id>/complete/` -> the object is verified and processing is queued
   (`services.uploads.complete_upload`).

Statuses: 422 for a rejected file (type, size, checksum, the daily limit), 402 while the account
is frozen, 429 from `RECEIPTS_UPLOAD_USER`, 404 for another user's receipt.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.billing.services import get_balance
from apps.core import ratelimit

from ..models import Receipt, ReceiptsSettings
from ..services import AccountFrozenError, complete_upload, start_upload
from .common import credits_url, json_body, render_page


@login_required
@require_GET
@ensure_csrf_cookie
def upload_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    config = ReceiptsSettings.load()
    return render_page(
        request,
        'receipts/upload.html',
        {
            'balance': get_balance(request.user),
            'price_per_file': config.price_per_file,
            'max_file_bytes': config.max_file_bytes,
            'allowed_types': config.allowed_types_list(),
            'start_url': reverse('receipts:upload-start'),
            'complete_url_template': reverse(
                'receipts:upload-complete', args=['00000000-0000-0000-0000-000000000000']
            ),
            'list_url': reverse('receipts:dashboard'),
        },
    )


@login_required
@require_POST
def upload_start(request: AuthenticatedHttpRequest) -> HttpResponse:
    limited = ratelimit.hit_user(request.user, 'RECEIPTS_UPLOAD_USER')
    if not limited.allowed:
        return ratelimit.json_response(limited)

    body = json_body(request)
    name = str(body.get('name', ''))
    content_type = str(body.get('content_type', ''))
    checksum = str(body.get('checksum_sha256', ''))
    try:
        size = int(body.get('size', 0))
    except TypeError, ValueError:
        return JsonResponse({'error': 'The file size must be a number.'}, status=422)
    if not name.strip():
        return JsonResponse({'error': 'The file needs a name.'}, status=422)

    try:
        ticket = start_upload(request.user, name, size, content_type, checksum)
    except AccountFrozenError as exc:
        return JsonResponse(
            {'error': exc.message, 'frozen': True, 'top_up_url': credits_url()}, status=402
        )
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)

    return JsonResponse(
        {
            'receipt_id': str(ticket.receipt.pk),
            'url': ticket.url,
            'headers': ticket.headers,
            'expires_in': ticket.expires_in,
        },
        status=201,
    )


@login_required
@require_POST
def upload_complete(request: AuthenticatedHttpRequest, receipt_id: str) -> HttpResponse:
    receipt = get_object_or_404(Receipt, pk=receipt_id, user=request.user)
    try:
        receipt = complete_upload(receipt)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)
    return JsonResponse(
        {
            'ok': True,
            'receipt_id': str(receipt.pk),
            'url': reverse('receipts:detail', args=[receipt.pk]),
        }
    )
