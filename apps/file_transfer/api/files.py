"""The JWT API's per-file upload endpoints (`/api/ft/transfers/{id}/files/...`, spec section 14):
add a file to a draft, request presigned multipart part URLs, list already-uploaded parts to
resume, complete a file's upload, and remove a file. A near-exact typed mirror of the web send
page's JSON endpoints (`apps.file_transfer.views.uploads`) -- same services, same rate limit, same
"draft owned by this caller" scoping -- just under `/api/ft/` and JWT-authenticated instead of
session-authenticated.
"""

import math
from uuid import UUID

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.core import ratelimit

from .. import services
from ..models import Transfer, TransferFile, TransferStatus
from ..schemas import (
    AddFileResponseSchema,
    AddFileSchema,
    CompleteFileSchema,
    OkSchema,
    PartUrlsRequestSchema,
    PartUrlsResponseSchema,
    ResumeResponseSchema,
)
from ..services.storage import PART_SIZE_BYTES
from .router import router

auth = JWTAuth()


def _part_count(size: int) -> int:
    return max(1, math.ceil(size / PART_SIZE_BYTES))


def _draft(request, transfer_id: UUID) -> Transfer:
    return get_object_or_404(
        Transfer, id=transfer_id, owner=request.auth, status=TransferStatus.DRAFT
    )


def _file(request, transfer_id: UUID, file_id: UUID) -> TransferFile:
    transfer = _draft(request, transfer_id)
    return get_object_or_404(TransferFile, id=file_id, transfer=transfer)


@router.post('/transfers/{transfer_id}/files/', response={201: AddFileResponseSchema}, auth=auth)
def add_file(request, transfer_id: UUID, payload: AddFileSchema):
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'FT_UPLOAD_USER'))
    transfer = _draft(request, transfer_id)
    try:
        file = services.add_file(
            transfer,
            payload.name,
            payload.size,
            client_last_modified=payload.client_last_modified,
        )
    except ValidationError as exc:
        raise HttpError(400, exc.messages[0])

    return 201, {
        'file_id': file.id,
        'part_size_bytes': PART_SIZE_BYTES,
        'part_count': _part_count(payload.size),
    }


@router.post(
    '/transfers/{transfer_id}/files/{file_id}/parts/',
    response=PartUrlsResponseSchema,
    auth=auth,
)
def part_urls(request, transfer_id: UUID, file_id: UUID, payload: PartUrlsRequestSchema):
    """Presigned PUT URLs for a batch of parts, with per-part checksums baked into the signature
    (spec section 11) -- see `services.uploads.presign_parts`'s docstring for why."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'FT_UPLOAD_USER'))
    file = _file(request, transfer_id, file_id)
    parts = [
        {'part_number': p.part_number, 'checksum_sha256': p.checksum_sha256} for p in payload.parts
    ]
    if not parts:
        raise HttpError(400, 'parts must not be empty.')

    try:
        urls = services.presign_parts(file, parts)
    except ValidationError as exc:
        raise HttpError(400, exc.messages[0])

    return {'urls': {str(number): url for number, url in urls.items()}}


@router.post(
    '/transfers/{transfer_id}/files/{file_id}/resume/',
    response=ResumeResponseSchema,
    auth=auth,
)
def resume_file(request, transfer_id: UUID, file_id: UUID):
    """Resumable uploads (spec section 2, issue #55 phase 3): tell the caller which parts of this
    file's multipart upload S3 already has, restarting the upload transparently if S3 no longer
    recognizes it (expired/aborted) -- see `apps.file_transfer.views.uploads.resume_file`, which
    this mirrors exactly, for the full reasoning."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'FT_UPLOAD_USER'))
    file = _file(request, transfer_id, file_id)

    try:
        parts = services.list_uploaded_parts(file)
        restarted = False
    except services.UploadExpired:
        file = services.restart_upload(file)
        parts = []
        restarted = True
    except ValidationError as exc:
        raise HttpError(400, exc.messages[0])

    return {
        'restarted': restarted,
        'part_size_bytes': PART_SIZE_BYTES,
        'part_count': _part_count(file.size),
        'uploaded_parts': [
            {'part_number': p['PartNumber'], 'etag': p['ETag'], 'size': p['Size']} for p in parts
        ],
    }


@router.post('/transfers/{transfer_id}/files/{file_id}/complete/', response=OkSchema, auth=auth)
def complete_file(request, transfer_id: UUID, file_id: UUID, payload: CompleteFileSchema):
    file = _file(request, transfer_id, file_id)
    parts = []
    for part in payload.parts:
        entry = {'PartNumber': part.part_number, 'ETag': part.etag}
        if part.checksum_sha256 is not None:
            entry['ChecksumSHA256'] = part.checksum_sha256
        parts.append(entry)
    if not parts:
        raise HttpError(400, 'parts must be a non-empty list.')

    try:
        services.complete_file_upload(file, parts)
    except ValidationError as exc:
        raise HttpError(400, exc.messages[0])

    return {'ok': True}


@router.delete('/transfers/{transfer_id}/files/{file_id}', response={204: None}, auth=auth)
def remove_file(request, transfer_id: UUID, file_id: UUID):
    file = _file(request, transfer_id, file_id)
    services.remove_file(file)
    return 204, None
