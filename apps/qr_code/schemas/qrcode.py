"""Pydantic schemas for QR code endpoints."""

from ninja import ModelSchema, Schema

from ..models import QRCode


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
    has_active_link: bool = False
    """Whether the code has a share link that works now (an active link on a code staff have not
    taken down)."""
    taken_down: bool = False
    """Staff took the QR code down (final): none of its share links work and it cannot be shared."""
    takedown_reason: str | None = None
    """Why it was taken down; `null` unless it was. Never the staff's internal note."""

    @staticmethod
    def resolve_has_active_link(obj) -> bool:
        return bool(getattr(obj, 'has_active_link', False))

    @staticmethod
    def resolve_taken_down(obj) -> bool:
        return obj.is_taken_down

    @staticmethod
    def resolve_takedown_reason(obj) -> str | None:
        return (obj.takedown_reason or None) if obj.is_taken_down else None

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
    """The preview as a PNG `data:` URI."""
