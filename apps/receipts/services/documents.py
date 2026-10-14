"""Turning stored bytes into something the AI can read: sniffing what a file really is, and
preparing images and text from it.

**Sniffing** trusts magic bytes only (`sniff_content_type`) -- never the filename or the type the
client claimed. HEIC and HEIF are both reported as `image/heic`.

**Preparing** (`prepare_input`):

- An image (JPEG, PNG, WebP, HEIC) is decoded with Pillow (`pillow-heif` registers the HEIC
  opener), rotated by its EXIF orientation, flattened onto white if it has transparency and
  downscaled to `MAX_IMAGE_SIDE_PX` on its long side, then re-encoded as JPEG. For a HEIC original
  that JPEG is also returned as the `preview_jpeg` to store, since browsers can't show HEIC.
- A PDF's text layer is read with `pypdf` under a low decompression cap (`PDF_TEXT_LIMITS`; a PDF
  over it is refused as unreadable); a scan (almost no text) is instead rendered with `pypdfium2`,
  the first `max_pdf_pages` pages only, and sent as images.
- An HTML or plain-text email body is converted to text with the stdlib parser (scripts, styles
  and other non-content elements dropped).

Anything that can't be decoded raises `UnreadableDocumentError`; a type with no preparation raises
`UnsupportedDocumentError`. An image whose header claims more than `MAX_IMAGE_PIXELS` is refused
before its pixel data is ever loaded (a decompression-bomb guard; Pillow's own process-wide limit
is left alone) and is shrunk to `MAX_IMAGE_SIDE_PX` *before* it is rotated or converted, so those
steps never copy the full-size bitmap. A PDF page is rendered at a scale that keeps its longer side
within `MAX_IMAGE_SIDE_PX` -- before anything is allocated -- and refused outright if its page size
is absurd.

All of this runs in the receipts worker, where an out-of-memory kill restarts the whole container,
so a small hostile file must not be able to make it allocate much more than its output.
"""

import io
import re
import warnings
from dataclasses import dataclass, field
from html.parser import HTMLParser

from PIL import Image, ImageOps, UnidentifiedImageError

#: Longest side, in pixels, of an image sent to the AI (and of a stored HEIC preview).
MAX_IMAGE_SIDE_PX = 2048
JPEG_QUALITY = 85

#: Images with more pixels than this are refused (the largest phone cameras write 48 MP).
MAX_IMAGE_PIXELS = 50_000_000

#: What gets sent to the AI is capped so one huge PDF or HTML body can't blow up a request.
MAX_TEXT_CHARS = 30_000
#: A PDF with less text than this is treated as a scan and rendered instead.
MIN_PDF_TEXT_CHARS = 40
#: Pages whose text is read from a PDF (the AI only needs the first ones of a long document).
MAX_PDF_TEXT_PAGES = 20
#: pypdf's limits on what a stream may decompress to, for reading a PDF's text. Its defaults allow
#: 75 MB per stream, and extracting text from one such stream took ~1 GB of memory; a real
#: receipt's content stream is a few KB. A PDF over these is refused (`LimitReachedError`), not
#: rendered instead: pdfium decodes the same stream with no limit of its own (a 300 MB one took
#: ~6 GB). Applied per call, for the current context only (`pypdf.apply_configuration`).
PDF_TEXT_LIMITS = {
    'zlib_maximum_output_length': 2_000_000,
    'lzw_maximum_output_length': 2_000_000,
    'run_length_maximum_output_length': 2_000_000,
    'brotli_maximum_output_length': 2_000_000,
    'array_based_stream_maximum_output_length': 2_000_000,
}
#: `pypdfium2` render scale: 1 is 72 dpi, so 2 is 144 dpi (a Letter page is ~1224x1584).
PDF_RENDER_SCALE = 2
#: A PDF page bigger than this on a side, in points (14400 pt is 200 inches, the PDF spec's own
#: maximum), is refused as unreadable instead of rendered.
MAX_PDF_PAGE_POINTS = 14_400

PDF = 'application/pdf'
JPEG = 'image/jpeg'
PNG = 'image/png'
WEBP = 'image/webp'
HEIC = 'image/heic'
HTML = 'text/html'
PLAIN = 'text/plain'

IMAGE_TYPES = frozenset({JPEG, PNG, WEBP, HEIC})

_HEVC_BRANDS = frozenset({b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'hevm', b'hevs'})
_GENERIC_HEIF_BRANDS = frozenset({b'mif1', b'msf1'})
_AVIF_BRANDS = frozenset({b'avif', b'avis'})

_HTML_HINT = re.compile(rb'<\s*(?:!doctype\s+html|html|head|body|div|p|table|span|br|a)\b', re.I)


class DocumentError(Exception):
    """Base class for a file that can't be prepared for the AI."""


class UnreadableDocumentError(DocumentError):
    """The file is corrupt, encrypted, too large to decode, or otherwise can't be read."""


class UnsupportedDocumentError(DocumentError):
    """The file is a type this pipeline can't prepare."""


@dataclass(slots=True)
class PreparedInput:
    """What `prepare_input` extracted: JPEG images and/or text for the AI, and a JPEG preview to
    store when the original (HEIC) can't be shown by a browser."""

    text: str = ''
    images: list[bytes] = field(default_factory=list)
    preview_jpeg: bytes | None = None


def sniff_content_type(data: bytes, *, allow_text: bool = False) -> str | None:
    """The content type `data` really is, from its leading bytes: `application/pdf`, `image/jpeg`,
    `image/png`, `image/webp` or `image/heic`; with `allow_text=True` also `text/html` (it looks
    like markup) or `text/plain` (valid UTF-8 without NUL bytes) -- for an email body, never an
    attachment. `None` when it is none of these."""
    head = data[:1024]
    if head.lstrip(b'\x00\t\r\n \xef\xbb\xbf').startswith(b'%PDF-'):
        return PDF
    if data.startswith(b'\xff\xd8\xff'):
        return JPEG
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return PNG
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return WEBP
    if data[4:8] == b'ftyp':
        brands = {data[8:12]}
        # The compatible-brands list: 4 bytes each after the 4-byte minor version.
        box_size = int.from_bytes(data[:4], 'big')
        for offset in range(16, min(box_size, len(data), 64) - 3, 4):
            brands.add(data[offset : offset + 4])
        if brands & _HEVC_BRANDS:
            return HEIC
        if brands & _GENERIC_HEIF_BRANDS and not brands & _AVIF_BRANDS:
            return HEIC
        return None
    if allow_text:
        if _HTML_HINT.search(head):
            return HTML
        if b'\x00' not in head:
            try:
                data[:4096].decode('utf-8')
            except UnicodeDecodeError:
                return None
            return PLAIN
    return None


# -- Images -------------------------------------------------------------------------------------

_heif_registered = False


def _ensure_heif() -> None:
    """Register `pillow-heif`'s opener with Pillow, once."""
    global _heif_registered
    if not _heif_registered:
        import pillow_heif

        pillow_heif.register_heif_opener()
        _heif_registered = True


def _normalize_image(data: bytes) -> bytes:
    """Decode an image and return it as a JPEG no larger than `MAX_IMAGE_SIDE_PX` on a side."""
    _ensure_heif()
    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width * height > MAX_IMAGE_PIXELS:
            raise UnreadableDocumentError('The image is too large to read.')
        if image.format == 'JPEG':
            # Lets libjpeg decode at a reduced size, which keeps a big photo's memory use down.
            image.draft('RGB', (MAX_IMAGE_SIDE_PX, MAX_IMAGE_SIDE_PX))
        image.load()
        return _encode_jpeg(image)
    except UnreadableDocumentError:
        raise
    except _IMAGE_ERRORS as exc:
        raise UnreadableDocumentError(f'The image could not be read: {exc}') from exc


_IMAGE_ERRORS = (
    UnidentifiedImageError,
    Image.DecompressionBombError,
    OSError,
    ValueError,
    SyntaxError,
    EOFError,
)


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """`(width, height)` of an image read from its header alone -- no pixel data is decoded, so a
    decompression bomb costs nothing here -- or `None` if it can't be opened."""
    _ensure_heif()
    try:
        with warnings.catch_warnings():
            # Pillow warns (and past twice its limit raises) when a header claims a huge image.
            warnings.simplefilter('ignore')
            with Image.open(io.BytesIO(data)) as image:
                return image.size
    except Exception:
        return None


def _encode_jpeg(image: Image.Image) -> bytes:
    """Orient, flatten and downscale an already decoded image, and encode it as a JPEG."""
    if image.mode in ('1', 'P', 'PA'):
        # Palette images can't be resampled with a real filter; expand them (<= 4 bytes a pixel).
        has_alpha = image.mode == 'PA' or 'transparency' in image.info
        image = image.convert('RGBA' if has_alpha else 'RGB')
    # Shrink first, in place (it keeps `info`, so the EXIF orientation survives): the rotation and
    # the conversions below then copy a bitmap of at most `MAX_IMAGE_SIDE_PX` on a side, not the
    # full-size one. The box is square, so rotating afterwards doesn't change what fits.
    image.thumbnail((MAX_IMAGE_SIDE_PX, MAX_IMAGE_SIDE_PX), Image.Resampling.LANCZOS)
    image = ImageOps.exif_transpose(image) or image
    if image.mode in ('RGBA', 'LA'):
        rgba = image.convert('RGBA')
        flattened = Image.new('RGB', rgba.size, (255, 255, 255))
        flattened.paste(rgba, mask=rgba.getchannel('A'))
        image = flattened
    elif image.mode != 'RGB':
        image = image.convert('RGB')
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


# -- PDFs ---------------------------------------------------------------------------------------


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import LimitReachedError

    try:
        with apply_configuration(None, **PDF_TEXT_LIMITS):
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted and not reader.decrypt(''):
                raise UnreadableDocumentError('The PDF is password protected.')
            parts: list[str] = []
            total = 0
            for page in reader.pages[:MAX_PDF_TEXT_PAGES]:
                text = (page.extract_text() or '').strip()
                if text:
                    parts.append(text)
                    total += len(text)
                if total >= MAX_TEXT_CHARS:
                    break
            return '\n\n'.join(parts)[:MAX_TEXT_CHARS]
    except UnreadableDocumentError:
        raise
    except LimitReachedError as exc:  # a stream far bigger than any receipt's
        raise UnreadableDocumentError('The PDF is too complex to read.') from exc
    except Exception as exc:  # pypdf raises a wide variety of errors on damaged files
        raise UnreadableDocumentError(f'The PDF could not be read: {exc}') from exc


def _render_scale(width_pt: float, height_pt: float) -> float:
    """The scale to render a `width_pt` x `height_pt` page at: `PDF_RENDER_SCALE`, lowered so the
    longer side of the bitmap is at most `MAX_IMAGE_SIDE_PX` (all the AI receives, so rendering
    more would only allocate it to throw it away). Refuses a page of absurd (or nonsensical) size."""
    if not (0 < width_pt <= MAX_PDF_PAGE_POINTS and 0 < height_pt <= MAX_PDF_PAGE_POINTS):
        raise UnreadableDocumentError('The PDF page size is not supported.')
    return min(float(PDF_RENDER_SCALE), MAX_IMAGE_SIDE_PX / max(width_pt, height_pt))


def _render_pdf_pages(data: bytes, max_pages: int) -> list[bytes]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise UnreadableDocumentError(f'The PDF could not be rendered: {exc}') from exc
    try:
        images: list[bytes] = []
        for index in range(min(len(pdf), max(1, max_pages))):
            page = pdf[index]
            try:
                width_pt, height_pt = page.get_size()
                scale = _render_scale(width_pt, height_pt)
                images.append(_encode_jpeg(page.render(scale=scale).to_pil()))
            finally:
                page.close()
        return images
    except UnreadableDocumentError:
        raise
    except Exception as exc:
        raise UnreadableDocumentError(f'The PDF could not be rendered: {exc}') from exc
    finally:
        pdf.close()


# -- HTML / text --------------------------------------------------------------------------------

_SKIP_TAGS = frozenset({'script', 'style', 'head', 'title', 'noscript', 'template', 'svg'})
_BLOCK_TAGS = frozenset(
    {
        'p', 'div', 'br', 'tr', 'table', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'section', 'article', 'header', 'footer', 'blockquote', 'pre', 'hr',
    }
)  # fmt: skip


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append('\n')
        elif tag in ('td', 'th'):
            self._parts.append(' ')

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append('\n')

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append('\n')

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        raw = ''.join(self._parts)
        lines = (re.sub(r'[ \t\r\f\v\xa0]+', ' ', line).strip() for line in raw.split('\n'))
        collapsed = re.sub(r'\n{2,}', '\n', '\n'.join(lines))
        return collapsed.strip()


def decode_text(data: bytes) -> str:
    """Bytes of a text body as a string: UTF-8, else Latin-1 (which never fails)."""
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('latin-1')


def html_to_text(html: bytes | str) -> str:
    """The visible text of an HTML document: scripts, styles and the head dropped, block elements
    turned into line breaks, whitespace collapsed. Capped at `MAX_TEXT_CHARS`."""
    if isinstance(html, bytes):
        html = decode_text(html)
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # the stdlib parser is lenient, but never let a body fail the receipt
        pass
    return parser.text()[:MAX_TEXT_CHARS]


# -- Entry point --------------------------------------------------------------------------------


def prepare_input(data: bytes, content_type: str, *, max_pdf_pages: int = 5) -> PreparedInput:
    """Prepare `data` (of the detected `content_type`) for the AI. See the module docstring."""
    if content_type in IMAGE_TYPES:
        jpeg = _normalize_image(data)
        return PreparedInput(images=[jpeg], preview_jpeg=jpeg if content_type == HEIC else None)

    if content_type == PDF:
        text = _pdf_text(data)
        if len(text.strip()) >= MIN_PDF_TEXT_CHARS:
            return PreparedInput(text=text)
        images = _render_pdf_pages(data, max_pdf_pages)
        if not images and not text.strip():
            raise UnreadableDocumentError('The PDF has no pages.')
        return PreparedInput(text=text, images=images)

    if content_type == HTML:
        text = html_to_text(data)
    elif content_type == PLAIN:
        text = decode_text(data)[:MAX_TEXT_CHARS].strip()
    else:
        raise UnsupportedDocumentError(f'Cannot read {content_type or "this file type"}.')
    if not text:
        raise UnreadableDocumentError('The message body is empty.')
    return PreparedInput(text=text)
