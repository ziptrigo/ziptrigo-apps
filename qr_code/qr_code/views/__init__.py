# Auth and Account pages are now handled by user-service; only QR-specific pages remain here.
from .pages import (
    dashboard,
    qrcode_duplicate,
    qrcode_editor,
)

__all__ = [
    'dashboard',
    'qrcode_duplicate',
    'qrcode_editor',
]
