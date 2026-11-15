"""JSON endpoints behind a file request's upload page (issue #306): add a file, presigned part
URLs, resume, complete and remove, under `/t/<slug>/upload/files/...`. The same shape as the
anonymous send endpoints (`views.anonymous`), against an open request instead of a session-owned
draft, for an uploader the access check (`services.request_access`) let through.

Every call spends `FT_REQUEST_UPLOAD_IP`; only once the request is found **and the access check
passed** does it spend `FT_REQUEST_UPLOAD_TRANSFER` (a made-up slug, or a visitor who may not use
the link, creates no key and spends none of it); a signed-in uploader then also spends
`FT_UPLOAD_USER`, shared with sending. **An uploader only ever reaches their own files**: another person's file id is a 404.
Block-listed uploaders (their IP, and their account email when signed in) get the neutral refusal
(403).
"""

import json

from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST

from apps.core import ratelimit
from apps.core.json_numbers import whole_number
from apps.core.services.client_ip import client_ip

from .. import services
from ..models import Transfer, TransferFile, TransferKind
from ..services.blocklist import BlockedSenderError
from ..services.request_access import Access, check_access
from ..services.request_uploader import Uploader, identify, remember_name
from .types import PublicHttpRequest
from .uploads import parse_parts


def _json_body(request: PublicHttpRequest) -> dict:
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _not_found() -> JsonResponse:
    return JsonResponse({'error': 'Not found.'}, status=404)


def _limited(request: PublicHttpRequest) -> HttpResponse | None:
    limited = ratelimit.hit_ip(request, 'FT_REQUEST_UPLOAD_IP')
    if not limited.allowed:
        return ratelimit.json_response(limited)
    return None


def _prelude(
    request: PublicHttpRequest, slug: str, *, create_identity: bool = False
) -> tuple[Transfer, Uploader] | HttpResponse:
    """What every endpoint starts with: the rate limits, the request behind `slug`, the access
    check and the uploader. Returns `(transfer, uploader)` or the response to send instead."""
    response = _limited(request)
    if response is not None:
        return response

    transfer = Transfer.objects.filter(slug=slug, kind=TransferKind.REQUEST).first()
    if transfer is None or not services.is_available(transfer):
        return _not_found()

    # The access check is a cheap lookup and comes before the per-request and per-user budgets:
    # someone who may not use the link (no password, not invited) must not be able to spend what
    # the real uploaders need. Only the per-IP rule above is charged for them.
    access = check_access(transfer, request.user, request.session)
    if access != Access.OK:
        return JsonResponse(
            {'error': 'You cannot upload to this link.', 'access': access.value}, status=403
        )

    result = ratelimit.hit_value(transfer.slug, 'FT_REQUEST_UPLOAD_TRANSFER')
    if not result.allowed:
        return ratelimit.json_response(result)
    if request.user.is_authenticated:
        result = ratelimit.hit_user(request.user, 'FT_UPLOAD_USER')
        if not result.allowed:
            return ratelimit.json_response(result)
    return transfer, identify(request.session, request.user, create=create_identity)


def _file(transfer: Transfer, uploader: Uploader, file_id: str) -> TransferFile | None:
    return services.request_upload.own_file(transfer, uploader, file_id)


def _refused(exc: ValidationError) -> JsonResponse:
    status = 403 if isinstance(exc, BlockedSenderError) else 422
    return JsonResponse({'error': exc.messages[0]}, status=status)


@require_POST
def add_file(request: PublicHttpRequest, slug: str) -> HttpResponse:
    prelude = _prelude(request, slug, create_identity=True)
    if isinstance(prelude, HttpResponse):
        return prelude
    transfer, uploader = prelude
    body = _json_body(request)
    name = str(body.get('name', ''))[:255].strip()
    size = whole_number(body.get('size', 0))
    if size is None:
        return JsonResponse({'error': 'size must be an integer.'}, status=422)
    if not name:
        return JsonResponse({'error': 'name is required.'}, status=422)
    client_last_modified = whole_number(body.get('client_last_modified'))

    if not uploader.is_signed_in and 'uploader_name' in body:
        # The anonymous uploader's optional name, kept for the session and stored with each file.
        remember_name(request.session, body.get('uploader_name'))
        uploader = identify(request.session, request.user, create=True)

    try:
        file = services.request_upload.add_file(
            transfer,
            uploader,
            name,
            size,
            ip=client_ip(request),
            client_last_modified=client_last_modified,
        )
    except ValidationError as exc:
        return _refused(exc)

    return JsonResponse(
        {
            'file_id': str(file.id),
            'part_size_bytes': file.part_size_bytes,
            'part_count': services.part_count_for(file),
        },
        status=201,
    )


@require_POST
def part_urls(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    prelude = _prelude(request, slug)
    if isinstance(prelude, HttpResponse):
        return prelude
    transfer, uploader = prelude
    file = _file(transfer, uploader, file_id)
    if file is None:
        return _not_found()
    parts = parse_parts(_json_body(request).get('parts', []))
    if parts is None:
        return JsonResponse(
            {'error': 'parts must be a list of {part_number, checksum_sha256}.'}, status=422
        )
    if not parts:
        return JsonResponse({'error': 'parts must not be empty.'}, status=422)

    try:
        urls = services.request_upload.presign_parts(transfer, file, parts)
    except ValidationError as exc:
        return _refused(exc)
    return JsonResponse({'urls': {str(number): url for number, url in urls.items()}})


@require_POST
def resume_file(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    prelude = _prelude(request, slug)
    if isinstance(prelude, HttpResponse):
        return prelude
    transfer, uploader = prelude
    file = _file(transfer, uploader, file_id)
    if file is None:
        return _not_found()

    try:
        file, parts, restarted = services.request_upload.resume_file(transfer, file)
    except ValidationError as exc:
        return _refused(exc)

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


@require_POST
def complete_file(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    prelude = _prelude(request, slug)
    if isinstance(prelude, HttpResponse):
        return prelude
    transfer, uploader = prelude
    file = _file(transfer, uploader, file_id)
    if file is None:
        return _not_found()
    parts = _json_body(request).get('parts')
    if not isinstance(parts, list) or not parts:
        return JsonResponse({'error': 'parts must be a non-empty list.'}, status=422)

    try:
        services.request_upload.complete_file(
            transfer, file, parts, uploader, ip=client_ip(request)
        )
    except ValidationError as exc:
        return _refused(exc)
    return JsonResponse({'ok': True})


@require_POST
def remove_file(request: PublicHttpRequest, slug: str, file_id: str) -> HttpResponse:
    prelude = _prelude(request, slug)
    if isinstance(prelude, HttpResponse):
        return prelude
    transfer, uploader = prelude
    file = _file(transfer, uploader, file_id)
    if file is None:
        return _not_found()
    try:
        services.request_upload.remove_own_file(transfer, file)
    except ValidationError as exc:
        return _refused(exc)
    return JsonResponse({'ok': True})
