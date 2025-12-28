"""Pydantic schemas for QR code endpoints."""

from ninja import ModelSchema, Schema
from src.qr_code.models import QRCode


class QRCodeCreateSchema(ModelSchema):
    """Schema for creating QR codes."""

    url: str | None = None
    data: str | None = None

    class Meta:
        model = QRCode
        fields = [
            'name',
            'qr_type',
            'qr_format',
            'size',
            'error_correction',
            'border',
            'background_color',
            'foreground_color',
            'use_url_shortening',
        ]


class QRCodeUpdateSchema(ModelSchema):
    """Schema for updating QR codes (name only)."""

    class Meta:
        model = QRCode
        fields = ['name']


class QRCodeSchema(ModelSchema):
    """Schema for QR code response."""

    image_url: str | None = None
    redirect_url: str | None = None

    class Meta:
        model = QRCode
        fields = [
            'id',
            'name',
            'qr_type',
            'content',
            'original_url',
            'use_url_shortening',
            'short_code',
            'qr_format',
            'size',
            'error_correction',
            'border',
            'background_color',
            'foreground_color',
            'image_file',
            'scan_count',
            'last_scanned_at',
            'created_at',
            'updated_at',
        ]


class QRCodePreviewSchema(Schema):
    """Schema for QR code preview response."""

    image_url: str
