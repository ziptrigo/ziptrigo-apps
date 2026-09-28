"""JSON endpoints backing the send page's direct-to-S3 multipart upload (spec section 2): add a
file to the draft, get presigned part URLs, complete the upload once every part is PUT, or remove
a file. Session-authenticated like every other view here, just not form-encoded HTMX: the browser
drives the upload against S3 itself and only talks to Django to coordinate it, so JSON in/out is
the natural fit (`CLAUDE.md`'s HTMX conventions are for actual form submissions).
"""

import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit

from .. import services
from ..models import Transfer, TransferFile, TransferStatus


def _json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return {}


def _draft(request: AuthenticatedHttpRequest, draft_id: str) -> Transfer:
    return get_object_or_404(Transfer, id=draft_id, owner=request.user, status=TransferStatus.DRAFT)


def _file(request: AuthenticatedHttpRequest, draft_id: str, file_id: str) -> TransferFile:
    transfer = _draft(request, draft_id)
    return get_object_or_404(TransferFile, id=file_id, transfer=transfer)


@login_required
@require_POST
def add_file(request: AuthenticatedHttpRequest, draft_id: str) -> HttpResponse:
    limited = ratelimit.hit_user(request.user, 'FT_UPLOAD_USER')
    if not limited.allowed:
        return ratelimit.json_response(limited)
    transfer = _draft(request, draft_id)
    body = _json_body(request)
    name = str(body.get('name', ''))[:255].strip()
    try:
        size = int(body.get('size', 0))
    except TypeError, ValueError:
        return JsonResponse({'error': 'size must be an integer.'}, status=422)

    if not name:
        return JsonResponse({'error': 'name is required.'}, status=422)

    client_last_modified = body.get('client_last_modified')
    try:
        client_last_modified = (
            int(client_last_modified) if client_last_modified is not None else None
        )
    except TypeError, ValueError:
        client_last_modified = None

    try:
        file = services.add_file(transfer, name, size, client_last_modified=client_last_modified)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)

    return JsonResponse(
        {
            'file_id': str(file.id),
            'part_size_bytes': file.part_size_bytes,
            'part_count': services.part_count_for(file),
        },
        status=201,
    )


@login_required
@require_POST
def part_urls(request: AuthenticatedHttpRequest, draft_id: str, file_id: str) -> HttpResponse:
    """Presigned PUT URLs for a batch of parts.

    Body: `{"parts": [{"part_number": n, "checksum_sha256": "<base64 SHA-256 of that part>"},
    ...]}` -- the browser computes each part's checksum with `crypto.subtle` before calling this,
    so the checksum can be bound into the presigned URL's signature (see
    `services.uploads.presign_parts`).
    """
    limited = ratelimit.hit_user(request.user, 'FT_UPLOAD_USER')
    if not limited.allowed:
        return ratelimit.json_response(limited)
    file = _file(request, draft_id, file_id)
    body = _json_body(request)
    raw_parts = body.get('parts', [])
    try:
        parts = [
            {'part_number': int(p['part_number']), 'checksum_sha256': str(p['checksum_sha256'])}
            for p in raw_parts
        ]
    except TypeError, ValueError, KeyError:
        return JsonResponse(
            {'error': 'parts must be a list of {part_number, checksum_sha256}.'}, status=422
        )

    if not parts:
        return JsonResponse({'error': 'parts must not be empty.'}, status=422)

    try:
        urls = services.presign_parts(file, parts)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)

    return JsonResponse({'urls': {str(number): url for number, url in urls.items()}})


@login_required
@require_POST
def complete_file(request: AuthenticatedHttpRequest, draft_id: str, file_id: str) -> HttpResponse:
    file = _file(request, draft_id, file_id)
    body = _json_body(request)
    parts = body.get('parts')
    if not isinstance(parts, list) or not parts:
        return JsonResponse({'error': 'parts must be a non-empty list.'}, status=422)

    try:
        services.complete_file_upload(file, parts)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)

    return JsonResponse({'ok': True})


@login_required
@require_POST
def remove_file(request: AuthenticatedHttpRequest, draft_id: str, file_id: str) -> HttpResponse:
    file = _file(request, draft_id, file_id)
    services.remove_file(file)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def resume_file(request: AuthenticatedHttpRequest, draft_id: str, file_id: str) -> HttpResponse:
    """Resumable uploads (spec section 2): tell the browser which parts of this file's multipart
    upload S3 already has, after a page reload, so it only PUTs what's missing. Transparently
    restarts the upload (a fresh upload id, no parts) if S3 no longer recognizes the old one --
    the bucket's lifecycle rule aborts an incomplete multipart upload after a day, so a sender who
    comes back later than that would otherwise get a permanent, unrecoverable error here.
    """
    limited = ratelimit.hit_user(request.user, 'FT_UPLOAD_USER')
    if not limited.allowed:
        return ratelimit.json_response(limited)
    file = _file(request, draft_id, file_id)

    try:
        parts = services.list_uploaded_parts(file)
        restarted = False
    except services.UploadExpired:
        file = services.restart_upload(file)
        parts = []
        restarted = True
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)

    return JsonResponse(
        {
            'restarted': restarted,
            'part_size_bytes': file.part_size_bytes,
            'part_count': services.part_count_for(file),
            'uploaded_parts': [
                {
                    'part_number': p['PartNumber'],
                    'etag': p['ETag'],
                    'size': p['Size'],
                    'checksum_sha256': p.get('ChecksumSHA256', ''),
                }
                for p in parts
            ],
        }
    )
