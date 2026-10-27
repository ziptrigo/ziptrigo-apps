"""A QR code image of some text, rendered in memory.

A small helper any product can use to offer "a QR code of this link" without importing another
product: `core` can't import `qr_code`, whose `QRCodeGenerator` renders a stored, customised code
(colours, error correction, format) and stays as it is. This one is deliberately plain: black on
white, a PNG, error correction level M.
"""

import io

import segno

#: Pixels per module (segno's `scale`) and quiet-zone width in modules. Generous bounds: a code of
#: a 2 KB URL is under 100 modules a side, and these keep the PNG a few hundred KB at most.
MAX_SCALE = 50
MAX_BORDER = 20


def render_png(content: str, *, scale: int = 6, border: int = 2) -> bytes:
    """The PNG bytes of a QR code encoding `content`.

    `scale` is the size of one module in pixels (1-50) and `border` the quiet zone in modules
    (0-20; the QR standard asks for 4, scanners cope with 2 on a screen or a print).

    Raises `ValueError` for a `scale` or `border` out of range, an empty `content`, or a `content`
    too long for any QR code (segno's `DataOverflowError` is a `ValueError`).
    """
    if not content:
        raise ValueError('content must not be empty')
    if not 1 <= scale <= MAX_SCALE:
        raise ValueError(f'scale must be between 1 and {MAX_SCALE}')
    if not 0 <= border <= MAX_BORDER:
        raise ValueError(f'border must be between 0 and {MAX_BORDER}')
    code = segno.make(content, error='m', micro=False)
    buffer = io.BytesIO()
    code.save(buffer, kind='png', scale=scale, border=border)
    return buffer.getvalue()
