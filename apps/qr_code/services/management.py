"""Creating QR codes and rendering previews. Shared by the web views and the API.

The content rules live here (not in a form or schema) so every entry point enforces them.
"""

import re
import uuid
from typing import Any

from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction

from apps.accounts.models import User

from ..models import QRCode, QRCodeType, generate_short_code
from .qrcode import QRCodeGenerator

MAX_CONTENT_LENGTH = 1000

SHORT_CODE_PATTERN = re.compile(r'^[A-Za-z0-9]{4,16}\Z')


def validate_content(qr_type: str, content: str) -> None:
    """Check the text/URL to encode.

    Raises:
        ValidationError: If it's empty, too long, or not a valid URL for a URL-type QR code.
    """
    if not content:
        raise ValidationError('Please provide the text or URL to encode.')
    if len(content) > MAX_CONTENT_LENGTH:
        raise ValidationError(f'Please keep the content to {MAX_CONTENT_LENGTH} characters.')
    if qr_type == QRCodeType.URL:
        try:
            URLValidator()(content)
        except ValidationError:
            raise ValidationError('Please enter a valid URL, e.g. https://example.com.')


def is_short_code_available(short_code: str) -> bool:
    """Whether ``short_code`` is well-formed and not taken by another QR code."""
    return (
        SHORT_CODE_PATTERN.fullmatch(short_code) is not None
        and not QRCode.objects.filter(short_code=short_code).exists()
    )


def new_short_code() -> str:
    """Generate a short code that isn't taken yet."""
    short_code = generate_short_code()
    while not is_short_code_available(short_code):
        short_code = generate_short_code()
    return short_code


def short_url(short_code: str) -> str:
    """The `/go/<short_code>` URL a tracked QR code encodes."""
    return QRCode(short_code=short_code).get_redirect_url() or ''


def create_qrcode(
    user: User,
    *,
    qr_type: str,
    content: str,
    use_url_shortening: bool = False,
    short_code: str | None = None,
    **fields: Any,
) -> QRCode:
    """Validate and save a QR code, and generate its image.

    Only URL-type QR codes keep an ``original_url`` and can use URL shortening. With shortening,
    the encoded content becomes the `/go/<short_code>` redirect URL. A requested ``short_code``
    (one the server issued to the editor earlier) is used when it's well-formed and still free;
    otherwise, including when another QR code takes it at the same moment, a new one is generated.

    Args:
        user: The owner.
        qr_type: A `QRCodeType` value.
        content: The text or URL to encode.
        use_url_shortening: Whether to encode a trackable `/go/<short_code>` link instead.
        short_code: Preferred short code; ignored unless shortening applies.
        **fields: Other `QRCode` fields (``name``, ``qr_format``, ``size``, ...).

    Raises:
        ValidationError: If the content is invalid; see `validate_content`.
    """
    validate_content(qr_type, content)
    is_url = qr_type == QRCodeType.URL
    use_url_shortening = use_url_shortening and is_url

    qrcode = QRCode(
        created_by=user,
        qr_type=qr_type,
        content=content,
        original_url=content if is_url else None,
        use_url_shortening=use_url_shortening,
        **fields,
    )
    requested = (
        short_code
        if use_url_shortening and short_code and is_short_code_available(short_code)
        else None
    )
    qrcode.short_code = requested
    try:
        with transaction.atomic():
            qrcode.save()
    except IntegrityError:
        if requested is None:
            raise
        # Someone took the requested code since we checked; let the model generate another.
        qrcode.short_code = None
        qrcode.save()

    # If using URL shortening, encode the shortened URL instead
    if qrcode.use_url_shortening and qrcode.short_code:
        qrcode.content = short_url(qrcode.short_code)
        qrcode.save(update_fields=['content'])

    qrcode.image_file = async_to_sync(QRCodeGenerator.generate_qr_code)(qrcode)
    qrcode.save(update_fields=['image_file'])
    return qrcode


def render_preview(
    user: User,
    *,
    qr_type: str,
    content: str,
    use_url_shortening: bool = False,
    short_code: str | None = None,
    **fields: Any,
) -> str:
    """Validate and render a preview without saving anything; return it as a PNG `data:` URI.

    With URL shortening and a ``short_code``, the preview encodes the same `/go/<short_code>` link
    `create_qrcode` would.

    Raises:
        ValidationError: If the content is invalid; see `validate_content`.
    """
    validate_content(qr_type, content)
    qrcode = QRCode(id=uuid.uuid4(), created_by=user, qr_type=qr_type, content=content, **fields)
    if use_url_shortening and qr_type == QRCodeType.URL and short_code:
        qrcode.content = short_url(short_code)

    return QRCodeGenerator.render_png_data_uri(qrcode)
