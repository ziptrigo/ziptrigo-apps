"""Pydantic schemas for API validation."""

from .auth import (
    AccountUpdateSchema,
    EmailConfirmSchema,
    LoginSchema,
    PasswordChangeSchema,
    PasswordResetRequestSchema,
    PasswordResetSchema,
    SignupSchema,
    TokenResponseSchema,
    UserResponseSchema,
)
from .qrcode import (
    QRCodeCreateSchema,
    QRCodePreviewSchema,
    QRCodeSchema,
    QRCodeUpdateSchema,
)

__all__ = [
    # Auth schemas
    'SignupSchema',
    'LoginSchema',
    'TokenResponseSchema',
    'EmailConfirmSchema',
    'PasswordResetRequestSchema',
    'PasswordResetSchema',
    'AccountUpdateSchema',
    'PasswordChangeSchema',
    'UserResponseSchema',
    # QRCode schemas
    'QRCodeCreateSchema',
    'QRCodeUpdateSchema',
    'QRCodeSchema',
    'QRCodePreviewSchema',
]
