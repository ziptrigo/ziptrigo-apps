"""Pydantic schemas for API validation."""

from .qrcode import (
    QRCodeCreateSchema,
    QRCodePreviewSchema,
    QRCodeSchema,
    QRCodeUpdateSchema,
)
from .share import (
    QRCodeShareEmailSchema,
    QRCodeShareLinkCreateSchema,
    QRCodeShareLinkSchema,
    QRCodeShareLinkUpdateSchema,
    QRCodeShareRecipientSchema,
)

__all__ = [
    'QRCodeCreateSchema',
    'QRCodeUpdateSchema',
    'QRCodeSchema',
    'QRCodePreviewSchema',
    'QRCodeShareEmailSchema',
    'QRCodeShareLinkCreateSchema',
    'QRCodeShareLinkSchema',
    'QRCodeShareLinkUpdateSchema',
    'QRCodeShareRecipientSchema',
]
