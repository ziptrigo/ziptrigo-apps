"""Real (tiny) files for the tests: images Pillow and pillow-heif generate, and PDFs built by hand
so `pypdf` finds a text layer in one and `pypdfium2` has pixels to render in the other."""

import io
import zlib
from functools import cache

import pillow_heif
from PIL import Image, ImageDraw

pillow_heif.register_heif_opener()


def _image(size: tuple[int, int] = (200, 300), color=(240, 240, 240)) -> Image.Image:
    image = Image.new('RGB', size, color)
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), 'RECEIPT 42.10', fill=(0, 0, 0))
    return image


def _encode(image: Image.Image, fmt: str) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


@cache
def jpeg_bytes(size: tuple[int, int] = (200, 300)) -> bytes:
    return _encode(_image(size), 'JPEG')


@cache
def png_bytes(size: tuple[int, int] = (200, 300)) -> bytes:
    return _encode(_image(size), 'PNG')


@cache
def png_with_alpha_bytes() -> bytes:
    image = Image.new('RGBA', (50, 50), (0, 0, 0, 0))
    return _encode(image, 'PNG')


@cache
def webp_bytes(size: tuple[int, int] = (200, 300)) -> bytes:
    return _encode(_image(size), 'WEBP')


@cache
def heic_bytes(size: tuple[int, int] = (200, 300)) -> bytes:
    return _encode(_image(size), 'HEIF')


@cache
def scanned_pdf_bytes() -> bytes:
    """An image-only PDF: no text layer, so it has to be rendered."""
    return _encode(_image((300, 400)), 'PDF')


def _assemble_pdf(objects: list[bytes]) -> bytes:
    """A PDF from its numbered objects (object 1 is the catalog), with a correct xref table."""
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n' % (len(objects) + 1)
    out += b'0000000000 65535 f \n'
    for offset in offsets:
        out += b'%010d 00000 n \n' % offset
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


@cache
def text_pdf_bytes(text: str = 'Staples receipt. Total 42.10 USD. Visa 4242. 2026-10-09') -> bytes:
    """A one-page PDF with `text` in its text layer."""
    stream = f'BT /F1 12 Tf 50 700 Td ({text}) Tj ET'.encode('latin-1')
    return _assemble_pdf(
        [
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
            b'/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
            b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        ]
    )


@cache
def inflating_pdf_bytes(decompressed_bytes: int) -> bytes:
    """A one-page PDF of a few KB whose one Flate content stream inflates to `decompressed_bytes`
    of (harmless, but numerous) text operators."""
    operator = b'BT /F1 1 Tf (x) Tj ET\n'
    stream = zlib.compress(operator * (decompressed_bytes // len(operator)), 9)
    return _assemble_pdf(
        [
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
            b'/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
            b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(stream)
            + stream
            + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        ]
    )


@cache
def blank_pages_pdf_bytes(pages: int, side_pt: int) -> bytes:
    """A tiny PDF of `pages` empty `side_pt` x `side_pt` point pages (no text layer)."""
    kids = b' '.join(b'%d 0 R' % (3 + i) for i in range(pages))
    return _assemble_pdf(
        [
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [' + kids + b'] /Count %d >>' % pages,
        ]
        + [b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] >>' % (side_pt, side_pt)] * pages
    )


HTML_BODY = (
    b'<html><head><title>x</title><style>p{color:red}</style><script>alert(1)</script></head>'
    b'<body><h1>Your receipt</h1><p>Staples</p><table><tr><td>Total</td><td>42.10</td></tr>'
    b'</table></body></html>'
)
