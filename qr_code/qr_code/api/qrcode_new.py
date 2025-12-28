"""Async QRCode endpoints for Django Ninja."""

import uuid

from asgiref.sync import sync_to_async
from ninja import Router
from ninja_jwt.authentication import AsyncJWTAuth

from src.qr_code.models import QRCode
from src.qr_code.schemas import (
    QRCodeCreateSchema,
    QRCodePreviewSchema,
    QRCodeSchema,
    QRCodeUpdateSchema,
)
from src.qr_code.services import QRCodeGenerator

router = Router()


@router.get('/', response=list[QRCodeSchema], auth=AsyncJWTAuth())
async def list_qrcodes(request):
    """List all QR codes for the authenticated user."""
    user = request.auth

    # Get QR codes excluding soft-deleted
    queryset = QRCode.objects.filter(created_by=user, deleted_at__isnull=True)
    qrcodes: list[QRCode] = await sync_to_async(lambda: list(queryset))()

    # Add computed fields (dynamic attributes for serialization)
    for qr in qrcodes:
        qr.image_url = QRCodeGenerator.get_file_url(qr.image_file)  # type: ignore[attr-defined]
        qr.redirect_url = qr.get_redirect_url()  # type: ignore[attr-defined]

    return qrcodes


@router.post('/', response={201: QRCodeSchema}, auth=AsyncJWTAuth())
async def create_qrcode(request, payload: QRCodeCreateSchema):
    """Create a new QR code."""
    user = request.auth

    # Extract write-only fields
    url = getattr(payload, 'url', None)
    data = getattr(payload, 'data', None)

    # Determine content
    validated_data = payload.dict(exclude={'url', 'data'})

    if url:
        validated_data['original_url'] = url
        if validated_data.get('use_url_shortening'):
            validated_data['content'] = url  # Temporary
        else:
            validated_data['content'] = url
    else:
        validated_data['content'] = data

    # Set the user
    validated_data['created_by'] = user

    # Create instance
    qrcode = await sync_to_async(QRCode.objects.create)(**validated_data)

    # If using URL shortening, update content to shortened URL
    if qrcode.use_url_shortening and qrcode.short_code:
        redirect_url = qrcode.get_redirect_url()
        if redirect_url:
            qrcode.content = redirect_url
            await sync_to_async(qrcode.save)(update_fields=['content'])

    # Generate QR code image
    image_path = await QRCodeGenerator.generate_qr_code(qrcode)
    qrcode.image_file = image_path
    await sync_to_async(qrcode.save)(update_fields=['image_file'])

    # Add computed fields (dynamic attributes for serialization)
    qrcode.image_url = QRCodeGenerator.get_file_url(image_path)  # type: ignore[attr-defined]
    qrcode.redirect_url = qrcode.get_redirect_url()  # type: ignore[attr-defined]

    return 201, qrcode


@router.get('/{qr_id}', response=QRCodeSchema, auth=AsyncJWTAuth())
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
    qrcode.image_url = QRCodeGenerator.get_file_url(qrcode.image_file)  # type: ignore[attr-defined]
    qrcode.redirect_url = qrcode.get_redirect_url()  # type: ignore[attr-defined]

    return qrcode


@router.put('/{qr_id}', response=QRCodeSchema, auth=AsyncJWTAuth())
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
    qrcode.image_url = QRCodeGenerator.get_file_url(qrcode.image_file)  # type: ignore[attr-defined]
    qrcode.redirect_url = qrcode.get_redirect_url()  # type: ignore[attr-defined]

    return qrcode


@router.patch('/{qr_id}', response=QRCodeSchema, auth=AsyncJWTAuth())
async def partial_update_qrcode(request, qr_id: uuid.UUID, payload: QRCodeUpdateSchema):
    """Partially update QR code (name only)."""
    return await update_qrcode(request, qr_id, payload)


@router.delete('/{qr_id}', response={204: None}, auth=AsyncJWTAuth())
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
    user = request.auth

    # Extract fields
    url = getattr(payload, 'url', None)
    data = getattr(payload, 'data', None)
    use_url_shortening = getattr(payload, 'use_url_shortening', False)

    if url:
        content = url
    else:
        content = data

    # If using URL shortening, get short code from request for preview
    if use_url_shortening and url:
        short_code = request.data.get('short_code')
        if short_code:
            base_url = request.build_absolute_uri('/')
            content = f'{base_url}go/{short_code}/'

    # Create temporary QR instance for preview
    # Use model_dump() to extract pydantic fields
    payload_data = payload.model_dump(exclude={'url', 'data'})
    qr_instance = QRCode(
        id=uuid.uuid4(),
        created_by=user,
        content=content,
        name=payload_data.get('name', 'Preview'),
        qr_type=payload_data.get('qr_type', 'url'),
        qr_format=payload_data.get('qr_format', 'png'),
        size=payload_data.get('size', 10),
        error_correction=payload_data.get('error_correction', 'M'),
        border=payload_data.get('border', 4),
        background_color=payload_data.get('background_color', '#FFFFFF'),
        foreground_color=payload_data.get('foreground_color', '#000000'),
        use_url_shortening=use_url_shortening,
        image_file='preview.png',
    )

    image_path = await QRCodeGenerator.generate_qr_code(qr_instance)
    image_url = QRCodeGenerator.get_file_url(image_path)

    return {'image_url': image_url}
