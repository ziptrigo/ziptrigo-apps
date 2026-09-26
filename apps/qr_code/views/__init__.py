from .delete import qrcode_delete
from .editor import qrcode_create_submit, qrcode_edit_submit, qrcode_preview
from .pages import (
    dashboard,
    qrcode_duplicate,
    qrcode_editor,
)
from .redirect import redirect_short_url

__all__ = [
    'dashboard',
    'qrcode_create_submit',
    'qrcode_delete',
    'qrcode_duplicate',
    'qrcode_edit_submit',
    'qrcode_editor',
    'qrcode_preview',
    'redirect_short_url',
]
