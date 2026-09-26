"""Async QRCode endpoints for Django Ninja."""

import uuid

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import AsyncJWTAuth

from .. import services
from ..models import QRCode
from ..schemas import (
    QRCodeCreateSchema,
    QRCodePreviewSchema,
    QRCodeSchema,
    QRCodeUpdateSchema,
)
from ..services import QRCodeGenerator

router = Router(tags=['QR Codes'])


@router.get('/', response=list[QRCodeSchema], auth=AsyncJWTAuth())
async def list_qrcodes(request):
    """List all QR codes for the authenticated user."""
    user = request.auth

    # Get QR codes excluding soft-deleted
    queryset = QRCode.objects.filter(created_by=user, deleted_at__isnull=True)
    qrcodes: list[QRCode] = await sync_to_async(lambda: list(queryset))()

    # Add computed fields (dynamic attributes for serialization)
    for qr in qrcodes:
        qr.image_url = QRCodeGenerator.get_file_url(qr.image_file)
        qr.redirect_url = qr.get_redirect_url()

    return qrcodes


@router.post('/', response={201: QRCodeSchema}, auth=AsyncJWTAuth())
async def create_qrcode(request, payload: QRCodeCreateSchema):
    """Create a new QR code."""
    user = request.auth

    # Extract write-only fields
    url = getattr(payload, 'url', None)
    data = getattr(payload, 'data', None)
    fields = payload.dict(exclude={'url', 'data'})

    try:
        qrcode = await sync_to_async(services.create_qrcode)(
            user, content=url or data or '', **fields
        )
    except ValidationError as e:
        raise HttpError(400, e.messages[0])

    # Add computed fields (dynamic attributes for serialization)
    qrcode.image_url = QRCodeGenerator.get_file_url(qrcode.image_file)
    qrcode.redirect_url = qrcode.get_redirect_url()

    return 201, qrcode


@router.get('/{uuid:qr_id}', response=QRCodeSchema, auth=AsyncJWTAuth())
async def retrieve_qrcode(request, qr_id: uuid.UUID):
    """Get details of a specific QR code."""
    user = request.auth

    try:
        qrcode = await sync_to_async(QRCode.objects.get)(
            id=qr_id, created_by=user, deleted_at__isnull=True
        )
    except QRCode.DoesNotExist:
        return 404, {'detail': 'QR code not found.'}

    # Add computed fields (dynamic attributes for serialization)
    qrcode.image_url = QRCodeGenerator.get_file_url(qrcode.image_file)
    qrcode.redirect_url = qrcode.get_redirect_url()

    return qrcode


@router.put('/{uuid:qr_id}', response=QRCodeSchema, auth=AsyncJWTAuth())
async def update_qrcode(request, qr_id: uuid.UUID, payload: QRCodeUpdateSchema):
    """Update QR code (name only)."""
    user = request.auth

    try:
        qrcode = await sync_to_async(QRCode.objects.get)(
            id=qr_id, created_by=user, deleted_at__isnull=True
        )
    except QRCode.DoesNotExist:
        return 404, {'detail': 'QR code not found.'}

    # Extract name from payload dict
    payload_dict = payload.dict()
    if 'name' in payload_dict and payload_dict['name'] is not None:
        qrcode.name = payload_dict['name']
        await sync_to_async(qrcode.save)(update_fields=['name'])

    # Add computed fields (dynamic attributes for serialization)
    qrcode.image_url = QRCodeGenerator.get_file_url(qrcode.image_file)
    qrcode.redirect_url = qrcode.get_redirect_url()

    return qrcode


@router.patch('/{uuid:qr_id}', response=QRCodeSchema, auth=AsyncJWTAuth())
async def partial_update_qrcode(request, qr_id: uuid.UUID, payload: QRCodeUpdateSchema):
    """Partially update QR code (name only)."""
    return await update_qrcode(request, qr_id, payload)


@router.delete('/{uuid:qr_id}', response={204: None}, auth=AsyncJWTAuth())
async def delete_qrcode(request, qr_id: uuid.UUID):
    """Soft delete a QR code."""
    user = request.auth

    try:
        qrcode = await sync_to_async(QRCode.objects.get)(
            id=qr_id, created_by=user, deleted_at__isnull=True
        )
    except QRCode.DoesNotExist:
        return 404, {'detail': 'QR code not found.'}

    await qrcode.asoft_delete()
    return 204, None


@router.post('/preview', response=QRCodePreviewSchema, auth=AsyncJWTAuth())
async def preview_qrcode(request, payload: QRCodeCreateSchema):
    """Generate a QR code image for preview without saving to DB."""
    url = getattr(payload, 'url', None)
    data = getattr(payload, 'data', None)
    fields = payload.dict(exclude={'url', 'data'})

    try:
        image_url = await sync_to_async(services.render_preview)(
            request.auth, content=url or data or '', **fields
        )
    except ValidationError as e:
        raise HttpError(400, e.messages[0])
    # A PNG `data:` URI; previews aren't written to disk.
    return {'image_url': image_url}
