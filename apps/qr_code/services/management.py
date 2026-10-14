"""Creating QR codes and rendering previews. Shared by the web views and the API.

The content rules live here (not in a form or schema) so every entry point enforces them.
"""

import io
import re
import uuid
from typing import Any

import segno
from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction

from apps.accounts.models import User

from ..models import (
    QRCode,
    QRCodeErrorCorrection,
    QRCodeFormat,
    QRCodeType,
    generate_short_code,
)
from .qrcode import QRCodeGenerator

MAX_CONTENT_LENGTH = 1000

# `size` is segno's scale (pixels per module) and `border` is in modules. The output is
# (modules + 2 * border) * size pixels a side and the image is built in memory, so unbounded values
# let one request use gigabytes. The editor and the model default use 10 and 4.
MIN_SIZE, MAX_SIZE = 1, 50
MIN_BORDER, MAX_BORDER = 0, 20
# ty can't see Django's `_meta`; this is the colour columns' max_length.
MAX_COLOR_LENGTH = QRCode._meta.get_field('foreground_color').max_length or 0  # ty: ignore[unresolved-attribute]

MAX_SHORT_CODE_LENGTH = 16
SHORT_CODE_PATTERN = re.compile(rf'^[A-Za-z0-9]{{4,{MAX_SHORT_CODE_LENGTH}}}\Z')


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


def _check_color(color: str, field: str, qr_format: str, *, dark: bool) -> None:
    """Raise unless segno can draw with ``color`` in ``qr_format`` (names, hex, `transparent`).

    Checked on its own, with the other colour fixed, only to name the offending field; the pair
    is checked together by `validate_render_options`.
    """
    if not isinstance(color, str) or not 0 < len(color) <= MAX_COLOR_LENGTH:
        raise ValidationError(f'Please choose a valid {field}.')
    # Ask segno itself (the same parsing the real render does) with a tiny code, so what passes
    # here is exactly what it will accept later.
    value = QRCodeGenerator._parse_color(color)
    colors = {'dark': value, 'light': 'white'} if dark else {'dark': 'black', 'light': value}
    try:
        segno.make('0', micro=False).save(io.BytesIO(), kind=qr_format, **colors)
    except ValueError, TypeError, AttributeError, IndexError:
        raise ValidationError(f'Please choose a valid {field} for a {qr_format.upper()} image.')


def _make(content: str, error_correction: str) -> Any:
    """`segno.make` exactly as the generator calls it; a too-big payload is a `ValidationError`.

    `MAX_CONTENT_LENGTH` counts characters but a QR code holds bytes: 1,000 emoji (4 bytes each)
    fit at no error-correction level.
    """
    try:
        return segno.make(content, error=error_correction, micro=False)
    except segno.DataOverflowError:
        raise ValidationError(
            'That is too long for a QR code at this error correction level. '
            'Please shorten it or choose a lower error correction level.'
        )


def check_content_fits(content: str, error_correction: str) -> None:
    """Raise `ValidationError` unless ``content`` fits a QR code at ``error_correction``."""
    _make(content, error_correction)


def validate_render_options(qrcode: QRCode, encoded: str | None = None) -> None:
    """Check the image options (size, border, format, error correction, colours) of ``qrcode``.

    Checked before anything is saved or rendered, for the web editor and the API alike. Ends with
    one dry render of what will really be encoded (``encoded``, default ``qrcode.content``; with
    URL shortening that is the `/go/<code>` link, not the original URL) with the real format,
    error correction and *both* colours, as the generator calls it (scale 1, no border: the
    bounds above keep the real ones safe and this stays cheap), because some combinations fail
    only together (both colours `transparent`) and the content may not fit at all.

    Raises:
        ValidationError: If an option is out of range, not one segno can render, or the content
            doesn't fit.
    """
    if qrcode.qr_format not in QRCodeFormat.values:
        raise ValidationError(f'Please choose a format: {", ".join(QRCodeFormat.values)}.')
    if qrcode.error_correction not in QRCodeErrorCorrection.values:
        raise ValidationError(
            f'Please choose an error correction level: {", ".join(QRCodeErrorCorrection.values)}.'
        )
    if isinstance(qrcode.size, bool) or not MIN_SIZE <= qrcode.size <= MAX_SIZE:
        raise ValidationError(f'Please keep the size between {MIN_SIZE} and {MAX_SIZE}.')
    if isinstance(qrcode.border, bool) or not MIN_BORDER <= qrcode.border <= MAX_BORDER:
        raise ValidationError(f'Please keep the border between {MIN_BORDER} and {MAX_BORDER}.')
    _check_color(qrcode.background_color, 'background color', qrcode.qr_format, dark=False)
    _check_color(qrcode.foreground_color, 'foreground color', qrcode.qr_format, dark=True)

    qr = _make(qrcode.content if encoded is None else encoded, qrcode.error_correction)
    try:
        qr.save(
            io.BytesIO(),
            kind=qrcode.qr_format,
            scale=1,
            border=0,
            dark=QRCodeGenerator._parse_color(qrcode.foreground_color),
            light=QRCodeGenerator._parse_color(qrcode.background_color),
        )
    except ValueError, TypeError, AttributeError, IndexError:
        raise ValidationError(
            f'Please choose colors that work together in a {qrcode.qr_format.upper()} image: '
            'the foreground and the background can not both be transparent.'
        )


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
        ValidationError: If the content is invalid (see `validate_content`) or an image option is
            (see `validate_render_options`).
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
    # The code encodes the short link, not the original URL. Its short code isn't chosen yet, so
    # check against the longest one a request can name.
    encoded = short_url('0' * MAX_SHORT_CODE_LENGTH) if use_url_shortening else content
    validate_render_options(qrcode, encoded)
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
        ValidationError: If the content or an image option is invalid; see `create_qrcode`.
    """
    validate_content(qr_type, content)
    qrcode = QRCode(id=uuid.uuid4(), created_by=user, qr_type=qr_type, content=content, **fields)
    if use_url_shortening and qr_type == QRCodeType.URL and short_code:
        qrcode.content = short_url(short_code)
    validate_render_options(qrcode)

    return QRCodeGenerator.render_png_data_uri(qrcode)
