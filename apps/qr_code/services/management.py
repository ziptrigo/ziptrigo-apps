"""Creating QR codes and rendering previews. Shared by the web views and the API."""

import re
import uuid
from typing import Any

from asgiref.sync import async_to_sync

from apps.accounts.models import User

from ..models import QRCode
from .qrcode import QRCodeGenerator

SHORT_CODE_PATTERN = re.compile(r'^[A-Za-z0-9]{4,16}\Z')


def is_short_code_available(short_code: str) -> bool:
    """Whether ``short_code`` is well-formed and not taken by another QR code."""
    return (
        SHORT_CODE_PATTERN.fullmatch(short_code) is not None
        and not QRCode.objects.filter(short_code=short_code).exists()
    )


def create_qrcode(
    user: User,
    *,
    content: str,
    original_url: str | None = None,
    use_url_shortening: bool = False,
    short_code: str | None = None,
    **fields: Any,
) -> QRCode:
    """Save a QR code and generate its image.

    With URL shortening, the encoded content becomes the `/go/<short_code>` redirect URL. A
    requested ``short_code`` (e.g. the one the editor already showed the user) is used when it's
    well-formed and free; otherwise the model generates one.

    Args:
        user: The owner.
        content: The text or URL to encode.
        original_url: The URL a shortened QR code redirects to.
        use_url_shortening: Whether to encode a trackable `/go/<short_code>` link instead.
        short_code: Preferred short code; ignored unless ``use_url_shortening``.
        **fields: Other `QRCode` fields (``name``, ``qr_type``, ``qr_format``, ``size``, ...).
    """
    qrcode = QRCode(
        created_by=user,
        content=content,
        original_url=original_url,
        use_url_shortening=use_url_shortening,
        **fields,
    )
    if use_url_shortening and short_code and is_short_code_available(short_code):
        qrcode.short_code = short_code
    qrcode.save()

    # If using URL shortening, encode the shortened URL instead
    if qrcode.use_url_shortening and qrcode.short_code:
        redirect_url = qrcode.get_redirect_url()
        if redirect_url:
            qrcode.content = redirect_url
            qrcode.save(update_fields=['content'])

    qrcode.image_file = async_to_sync(QRCodeGenerator.generate_qr_code)(qrcode)
    qrcode.save(update_fields=['image_file'])
    return qrcode


def render_preview(
    user: User,
    *,
    content: str,
    use_url_shortening: bool = False,
    short_code: str | None = None,
    **fields: Any,
) -> str:
    """Generate a preview image without saving a QR code, and return the image's URL.

    With URL shortening and a well-formed ``short_code``, the preview encodes the same
    `/go/<short_code>` link `create_qrcode` would.
    """
    qrcode = QRCode(
        id=uuid.uuid4(),
        created_by=user,
        content=content,
        use_url_shortening=use_url_shortening,
        **fields,
    )
    if use_url_shortening and short_code and SHORT_CODE_PATTERN.fullmatch(short_code):
        qrcode.short_code = short_code
        qrcode.content = qrcode.get_redirect_url() or content

    image_path = async_to_sync(QRCodeGenerator.generate_qr_code)(qrcode)
    return QRCodeGenerator.get_file_url(image_path)
