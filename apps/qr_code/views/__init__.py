from .delete import qrcode_delete
from .editor import qrcode_create_submit, qrcode_edit_submit, qrcode_preview, qrcode_short_code
from .pages import (
    dashboard,
    qrcode_duplicate,
    qrcode_editor,
    qrcode_image,
)
from .public import shared_image, shared_page
from .redirect import redirect_short_url
from .share import (
    share_create,
    share_edit,
    share_email,
    share_panel,
    share_resend,
    share_revoke,
)

__all__ = [
    'dashboard',
    'qrcode_create_submit',
    'qrcode_delete',
    'qrcode_duplicate',
    'qrcode_edit_submit',
    'qrcode_editor',
    'qrcode_image',
    'qrcode_preview',
    'qrcode_short_code',
    'redirect_short_url',
    'share_create',
    'share_edit',
    'share_email',
    'share_panel',
    'share_resend',
    'share_revoke',
    'shared_image',
    'shared_page',
]
