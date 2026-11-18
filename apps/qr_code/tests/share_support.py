"""Helpers for the share tests: users, QR codes and share links in the states the tests need."""

import secrets
import uuid

from apps.accounts.models import User
from apps.qr_code.models import QRCode, QRCodeFormat, QRCodeType, QRShareLink


def make_user(**fields) -> User:
    """A confirmed, active user without a password (so no slow password hashing)."""
    fields.setdefault('email', f'user-{uuid.uuid4().hex[:8]}@example.com')
    fields.setdefault('email_confirmed', True)
    return User.objects.create_user(**fields)


def make_qrcode(user: User, **fields) -> QRCode:
    fields.setdefault('name', 'Shop door')
    fields.setdefault('content', 'https://example.com/shop')
    fields.setdefault('qr_type', QRCodeType.URL)
    fields.setdefault('qr_format', QRCodeFormat.PNG)
    fields.setdefault('image_file', 'x.png')
    return QRCode.objects.create(created_by=user, **fields)


def make_link(qrcode: QRCode, **fields) -> QRShareLink:
    """A share link straight in the database (no services): active by default."""
    fields.setdefault('code', secrets.token_urlsafe(16))
    return QRShareLink.objects.create(qrcode=qrcode, **fields)
