"""Turning stored bytes into something the AI can read: sniffing what a file really is, and
preparing images and text from it.

**Sniffing** trusts magic bytes only (`sniff_content_type`) -- never the filename or the type the
client claimed. HEIC and HEIF are both reported as `image/heic`.

**Preparing** (`prepare_input`):

- An image (JPEG, PNG, WebP, HEIC) is decoded with Pillow (`pillow-heif` registers the HEIC
  opener), rotated by its EXIF orientation, flattened onto white if it has transparency and
  downscaled to `MAX_IMAGE_SIDE_PX` on its long side, then re-encoded as JPEG. For a HEIC original
  that JPEG is also returned as the `preview_jpeg` to store, since browsers can't show HEIC.
- A PDF's text layer is read with `pypdf` under a low decompression cap (`PDF_TEXT_LIMITS`, **per
  stream**; a PDF over it is refused as too complex); a scan (almost no text) is instead rendered
  with `pypdfium2`, the first `max_pdf_pages` pages only, and sent as images.
- An HTML or plain-text email body is converted to text with the stdlib parser (scripts, styles
  and other non-content elements dropped).

Anything that can't be decoded raises `UnreadableDocumentError`; a type with no preparation raises
`UnsupportedDocumentError`; a file that needs more memory or time than the budget below allows (or
is plainly absurd: a header claiming more than `MAX_IMAGE_PIXELS`, a PDF page of absurd size, a
stream over the cap) raises `DocumentTooComplexError`, a subclass of `UnreadableDocumentError`
that the caller can tell apart because "try a sharper photo" is the wrong advice for it. An image
header claiming more than `MAX_IMAGE_PIXELS` is refused before its pixel data is loaded, and an
image is shrunk to `MAX_IMAGE_SIDE_PX` *before* it is rotated or converted, so those steps never
copy the full-size bitmap. A PDF page is rendered at a scale that keeps its longer side within
`MAX_IMAGE_SIDE_PX`.

**The budget** (the point of this module's shape). All of this runs in the receipts worker, where
an out-of-memory kill restarts the whole container, and the checks above only bound *one* thing
each: the decompression cap is per stream, and pdfium has no budget at all (a 5 KB PDF whose page
draws one 1.9 MB Form XObject 40 times needs 1.4 GB, because the renderer decodes the form again
for every use; 40 different forms do the same; and many big streams cost pypdf minutes of CPU).
No per-object cap can bound a renderer, so `prepare_input` runs the whole preparation in a child
process (`_prepare_in_child`) with a hard address-space limit (`RLIMIT_AS`, `DOCUMENT_MEMORY_MB`,
Linux only), a CPU-time limit (`RLIMIT_CPU`, `DOCUMENT_CPU_SECONDS`) and a wall-clock timeout
(`DOCUMENT_TIMEOUT_SECONDS`, a multiple of the CPU limit: CPU time does not grow with the load on
the host, so it is what refuses a CPU-heavy file, and the wall clock only catches a child that
hangs). The child is this very file run as a script (`python -I documents.py`): importing
`apps.receipts.services.documents` would import the whole Django-dependent services package, so
the file has no relative imports and needs none of it. It takes the bytes on stdin and answers JSON
on stdout, never touches the database, runs in its own session and with a minimal environment, and
asks the kernel's OOM killer to pick it before anything else (`oom_score_adj`). Where `RLIMIT_AS`
isn't enforced (macOS, so a developer machine) only the CPU limit and the timeout bound it.

**Two kinds of failure** (`prepare_input`'s contract). Only an outcome that means *this file is
over the budget* is a `DocumentTooComplexError` (a permanent `too_large`): the child died of
SIGABRT (an allocation failed in native code), SIGSEGV, SIGBUS or SIGXCPU (the CPU limit), raised
`MemoryError`, or said `too_complex`. Anything that says nothing about the file -- an ordinary
non-zero exit, no or a malformed reply, SIGINT/SIGTERM/SIGHUP, **SIGKILL** (with `RLIMIT_AS` a
file over its budget fails an allocation instead; SIGKILL is the kernel's OOM killer, i.e. memory
pressure on the container or host whoever caused it), the wall-clock timeout, the child failing to
start or to import a library -- is a `DocumentChildError`, which is
*not* an `UnreadableDocumentError`: the caller treats it as a transient failure (retried, bounded
by `max_ai_attempts`), so a broken image or a mistaken setting does not permanently fail every
receipt, and a hostile file still costs only a few budgets. Both the child and the in-process path
(`RECEIPTS_DOCUMENT_SANDBOX` off) go through `_prepare_guarded`, which maps what a library throws
the same way, so the unit tests exercise the production branches.

**This is a resource budget, not an isolation boundary.** The child gets no keys in its
environment, but it is an ordinary process: when the worker runs as root it drops to an
unprivileged user (see `_unprivileged_ids`; if it may not switch, it warns and runs the child as
itself), and that is all. It can still use the network and read
whatever that user can read; a memory-corruption bug in pdfium, libheif or libde265 is not
contained by anything here. Keep secret files unreadable by other users (mode 0600).

`RECEIPTS_DOCUMENT_SANDBOX` (settings; off under pytest so unit tests stay in-process and can
monkeypatch internals) switches the child off, `_prepare_unsandboxed` being the raw in-process work.
"""

import base64
import io
import json
import logging
import os
import re
import signal
import subprocess
import sys
import warnings
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

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
#: rendered instead: pdfium decodes the same stream with no limit of its own. **Per stream**: many
#: streams under it add up, which is what the child process's budget below is for. Applied per
#: call, for the current context only (`pypdf.apply_configuration`).
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

#: The budget of the child process `prepare_input` runs in (see the module docstring); the settings
#: `RECEIPTS_DOCUMENT_MEMORY_MB`, `RECEIPTS_DOCUMENT_CPU_SECONDS` and
#: `RECEIPTS_DOCUMENT_TIMEOUT_SECONDS` override them (`apps.receipts.checks` bounds them).
#: Address space, not resident memory (that is what `RLIMIT_AS` counts). Measured on Linux with
#: pillow 12.3, pypdfium2 5.14 and pillow-heif 1.8: the interpreter and libraries map under 96 MB,
#: an ordinary receipt needs 96-128 MB, and the worst legitimate inputs, a 48-megapixel HEIC or a
#: 50-megapixel PNG, pass at 512 MB. So this leaves 2x over the worst; the shared-form PDF needs
#: 1.4 GB and is refused. Lower it (setting) if the container has less room than that to spare.
DOCUMENT_MEMORY_MB = 1024
#: CPU seconds the child may use. Ordinary receipts need 0.1-1.5 s (a few pages of a big scan, a
#: 48-megapixel photo, a few more), so this leaves an order of magnitude; a hostile file costs the
#: worker at most this much of CPU per try.
DOCUMENT_CPU_SECONDS = 25
#: Wall-clock seconds before the child is killed: 2.4x the CPU limit, so that on a busy host the
#: CPU limit (which load does not change) refuses a CPU-heavy file, and this only catches a child
#: that hangs without using CPU.
DOCUMENT_TIMEOUT_SECONDS = 60.0

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


class DocumentTooComplexError(UnreadableDocumentError):
    """The file needs more memory or time than preparing a receipt may use (or exceeds a hard
    size limit): not corrupt, just too much. Unlike a plain unreadable file, a sharper photo
    would not help; a smaller or simpler version would."""


class UnsupportedDocumentError(DocumentError):
    """The file is a type this pipeline can't prepare."""


class DocumentChildError(DocumentError):
    """Preparing the file failed in a way that says nothing about the file: the child process
    exited abnormally or gave no usable answer, was stopped by an outside signal, hit the
    wall-clock timeout, or could not import a library. Deliberately *not* an
    `UnreadableDocumentError`: the caller retries (a bounded number of times) instead of failing
    the receipt for good."""


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
            raise DocumentTooComplexError('The image is too large to read.')
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
        # A bilevel or palette image can only be resampled with NEAREST. Shrink it that way to at
        # most twice the target first (the expansion below, up to 4 bytes a pixel, is then small
        # whatever the image's size) and let the real filter below finish the job.
        image.thumbnail((2 * MAX_IMAGE_SIDE_PX, 2 * MAX_IMAGE_SIDE_PX), Image.Resampling.NEAREST)
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


class _RenderFailedError(UnreadableDocumentError):
    """pdfium could not open or render the PDF. Usually the file is corrupt, but pdfium reports a
    failed allocation this way too (not as a `MemoryError`), which is why the child looks at how
    much address space it had used when one of these is raised (`_reply_for`)."""


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
        raise DocumentTooComplexError('The PDF is too complex to read.') from exc
    except Exception as exc:  # pypdf raises a wide variety of errors on damaged files
        raise UnreadableDocumentError(f'The PDF could not be read: {exc}') from exc


def _render_scale(width_pt: float, height_pt: float) -> float:
    """The scale to render a `width_pt` x `height_pt` page at: `PDF_RENDER_SCALE`, lowered so the
    longer side of the bitmap is at most `MAX_IMAGE_SIDE_PX` (all the AI receives, so rendering
    more would only allocate it to throw it away). Refuses a page of absurd (or nonsensical) size."""
    if not (0 < width_pt <= MAX_PDF_PAGE_POINTS and 0 < height_pt <= MAX_PDF_PAGE_POINTS):
        raise DocumentTooComplexError('The PDF page size is not supported.')
    return min(float(PDF_RENDER_SCALE), MAX_IMAGE_SIDE_PX / max(width_pt, height_pt))


def _render_pdf_pages(data: bytes, max_pages: int) -> list[bytes]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise _RenderFailedError(f'The PDF could not be rendered: {exc}') from exc
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
        raise _RenderFailedError(f'The PDF could not be rendered: {exc}') from exc
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

_SUPPORTED_TYPES = IMAGE_TYPES | {PDF, HTML, PLAIN}


def prepare_input(data: bytes, content_type: str, *, max_pdf_pages: int = 5) -> PreparedInput:
    """Prepare `data` (of the detected `content_type`) for the AI. See the module docstring: unless
    `settings.RECEIPTS_DOCUMENT_SANDBOX` is off it runs in a child process under a memory, CPU and
    time budget. Raises `UnreadableDocumentError` for a file that can't be read,
    `DocumentTooComplexError` (a subclass) for one over the budget, `UnsupportedDocumentError` for
    a type it doesn't handle and `DocumentChildError` when the failure says nothing about the file
    (the caller retries)."""
    if content_type not in _SUPPORTED_TYPES:
        raise UnsupportedDocumentError(f'Cannot read {content_type or "this file type"}.')
    from django.conf import settings

    if not getattr(settings, 'RECEIPTS_DOCUMENT_SANDBOX', True):
        return _prepare_guarded(data, content_type, max_pdf_pages=max_pdf_pages)
    return _prepare_in_child(
        data,
        content_type,
        max_pdf_pages=max_pdf_pages,
        memory_mb=getattr(settings, 'RECEIPTS_DOCUMENT_MEMORY_MB', DOCUMENT_MEMORY_MB),
        cpu_seconds=getattr(settings, 'RECEIPTS_DOCUMENT_CPU_SECONDS', DOCUMENT_CPU_SECONDS),
        timeout=getattr(settings, 'RECEIPTS_DOCUMENT_TIMEOUT_SECONDS', DOCUMENT_TIMEOUT_SECONDS),
        child_user=getattr(settings, 'RECEIPTS_DOCUMENT_CHILD_USER', CHILD_USER),
    )


_OVER_MEMORY = 'The file needs more memory than allowed.'


def _caused_by_memory_error(exc: BaseException | None) -> bool:
    """Whether a `MemoryError` is anywhere in `exc`'s `__cause__` / `__context__` chain: the
    readers above wrap whatever a library raises (a failed allocation included) in their own
    error."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, MemoryError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _prepare_guarded(data: bytes, content_type: str, *, max_pdf_pages: int = 5) -> PreparedInput:
    """`_prepare_unsandboxed` with what a library throws mapped to this module's errors, the one
    place that decides it: the child (production) and the in-process path (tests) both call this,
    so they can't disagree. A `MemoryError`, or an error caused by one, is over the budget
    (`DocumentTooComplexError`); a library that could not be imported is the installation's fault,
    not the file's (`DocumentChildError`); any other unforeseen exception is taken to be the file's
    (`UnreadableDocumentError`)."""
    try:
        return _prepare_unsandboxed(data, content_type, max_pdf_pages=max_pdf_pages)
    except UnsupportedDocumentError, DocumentTooComplexError:
        raise
    except UnreadableDocumentError as exc:
        if _caused_by_memory_error(exc):
            raise DocumentTooComplexError(_OVER_MEMORY) from exc
        raise
    except ImportError as exc:
        raise DocumentChildError(f'A library could not be loaded: {exc}') from exc
    except Exception as exc:  # a library failing in a way nobody foresaw: the file is the cause
        if _caused_by_memory_error(exc):
            raise DocumentTooComplexError(_OVER_MEMORY) from exc
        raise UnreadableDocumentError(f'The file could not be read: {exc}') from exc


def _prepare_unsandboxed(
    data: bytes, content_type: str, *, max_pdf_pages: int = 5
) -> PreparedInput:
    """The work of `prepare_input`, in this process and under no budget."""
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


# -- The child process --------------------------------------------------------------------------
#
# Protocol: the parent writes one JSON line (`content_type`, `max_pdf_pages`, `memory_mb`,
# `cpu_seconds`) and then the file's bytes to the child's stdin; the child answers one JSON
# document on stdout: `{"ok": {"text", "images": [base64], "preview_jpeg": base64 | null}}` or
# `{"error": "unreadable" | "too_complex" | "unsupported" | "failed", "message"}`. Anything else (a
# non-zero exit, a signal, no or malformed output) means the child died; `_prepare_in_child` tells
# a death that means "over the budget" from the rest.

_CHILD_ENV_KEYS = ('PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'SYSTEMROOT')
#: Run the child as this user when the worker is root (settings: `RECEIPTS_DOCUMENT_CHILD_USER`;
#: empty keeps the worker's own user).
CHILD_USER = 'nobody'
#: How the OS ends a child that is over the budget: a failed allocation in native code under
#: `RLIMIT_AS` (SIGABRT), a crash, the CPU limit. **Not SIGKILL**: with the limits above a file
#: over its budget never reaches the OOM killer, so a SIGKILL means memory pressure on the
#: container or host (the child is its first victim, `oom_score_adj`), which a retry may survive.
#: Any other signal (SIGINT, SIGTERM, SIGHUP, ...) came from outside and says nothing about the
#: file either.
_BUDGET_SIGNALS = frozenset({signal.SIGABRT, signal.SIGSEGV, signal.SIGBUS, signal.SIGXCPU})
#: Reply kinds a child may send, and what each means to the caller.
_REPLY_ERRORS: dict[object, type[DocumentError]] = {
    'unreadable': UnreadableDocumentError,
    'too_complex': DocumentTooComplexError,
    'unsupported': UnsupportedDocumentError,
    'failed': DocumentChildError,
}
_LOGGED_MESSAGE_CHARS = 200
#: After the CPU limit (SIGXCPU) the kernel sends SIGKILL this many seconds later.
_CPU_GRACE_SECONDS = 2


_warned_missing_user = False


def _unprivileged_ids(user: str) -> dict:
    """`subprocess` arguments that run the child as `user` with no supplementary groups, when this
    process is root and `user` exists; `{}` (the child keeps our identity) otherwise, e.g. on a
    developer machine."""
    global _warned_missing_user
    if not user or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return {}
    import pwd

    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        if not _warned_missing_user:
            _warned_missing_user = True
            logger.warning(
                'RECEIPTS_DOCUMENT_CHILD_USER %r does not exist; the document child runs as root',
                user,
            )
        return {}
    return {'user': entry.pw_uid, 'group': entry.pw_gid, 'extra_groups': []}


def _describe_exit(returncode: int) -> str:
    """How a child ended, for the log: a signal's name or the exit status. Never anything the
    child wrote."""
    if returncode < 0:
        try:
            return f'signal {signal.Signals(-returncode).name}'
        except ValueError:
            return f'signal {-returncode}'
    return f'exit status {returncode}'


def _prepare_in_child(
    data: bytes,
    content_type: str,
    *,
    max_pdf_pages: int,
    memory_mb: int,
    cpu_seconds: float,
    timeout: float,
    child_user: str = '',
    command: list[str] | None = None,
) -> PreparedInput:
    """Run `_prepare_guarded` in a child process; see the module docstring. `command` replaces the
    child's command line (tests only)."""
    request = {
        'content_type': content_type,
        'max_pdf_pages': max_pdf_pages,
        'memory_mb': memory_mb,
        'cpu_seconds': cpu_seconds,
    }
    command = command or [sys.executable, '-I', str(Path(__file__).resolve())]
    # Only what the interpreter and its libraries need: no keys, tokens or database credentials in
    # a process that decodes files strangers sent.
    env = {key: os.environ[key] for key in _CHILD_ENV_KEYS if key in os.environ}
    payload = json.dumps(request).encode() + b'\n' + data

    def run(identity: dict) -> subprocess.CompletedProcess:
        # On a timeout, `run` kills the child and waits for it before raising. The child's own
        # session keeps a terminal's or process group's signals (Ctrl-C on a local worker) from
        # reaching it; `RLIMIT_CPU` still ends one whose worker is gone. Its stderr is dropped
        # (the child answers on stdout and silences its libraries): it is unbounded and can echo
        # the file, so nothing of it is kept or logged.
        return subprocess.run(
            command,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=env,
            check=False,
            start_new_session=True,
            **identity,
        )

    try:
        identity = _unprivileged_ids(child_user)
        try:
            result = run(identity)
        except PermissionError:
            if not identity:
                raise
            # Not allowed to switch user (a container without CAP_SETUID/CAP_SETGID), or the
            # user can't run the interpreter: better a child with the worker's own identity than
            # every receipt failing to start one.
            logger.warning(
                "Could not start the document child as %r; running it as the worker's user",
                child_user,
            )
            result = run({})
    except subprocess.TimeoutExpired as exc:
        logger.error('Preparing a %s file took more than %s s; child killed', content_type, timeout)
        raise DocumentChildError('Reading the file took too long.') from exc
    except OSError as exc:  # could not even start it
        logger.error('Could not start the document child: %s', type(exc).__name__)
        raise DocumentChildError('Reading the file could not be started.') from exc

    reply = _parse_reply(result.stdout) if result.returncode == 0 else None
    if reply is None:
        exit_description = _describe_exit(result.returncode)
        if result.returncode < 0 and -result.returncode in _BUDGET_SIGNALS:
            logger.warning(
                'The child preparing a %s file (%d bytes) ended with %s: over its budget '
                '(%d MB, %s CPU s)',
                content_type,
                len(data),
                exit_description,
                memory_mb,
                cpu_seconds,
            )
            raise DocumentTooComplexError('The file needs more memory or time than is allowed.')
        logger.error(
            'The child preparing a %s file (%d bytes) ended with %s and no usable answer',
            content_type,
            len(data),
            exit_description,
        )
        raise DocumentChildError('Reading the file failed.')

    if reply.get('ok') is not None:
        ok = reply['ok']
        try:
            return PreparedInput(
                text=ok['text'],
                images=[base64.b64decode(image, validate=True) for image in ok['images']],
                preview_jpeg=(
                    base64.b64decode(ok['preview_jpeg'], validate=True)
                    if ok['preview_jpeg'] is not None
                    else None
                ),
            )
        except ValueError as exc:  # invalid base64 (`binascii.Error` is one)
            logger.error('The child preparing a %s file sent undecodable data', content_type)
            raise DocumentChildError('Reading the file failed.') from exc
    error = _REPLY_ERRORS[reply['error']]
    message = str(reply.get('message', 'The file could not be read.'))
    if error is DocumentChildError:
        logger.error(
            'The child preparing a %s file reported a failure: %s',
            content_type,
            message[:_LOGGED_MESSAGE_CHARS],
        )
        message = 'Reading the file failed.'
    raise error(message)


def _parse_reply(stdout: bytes) -> dict | None:
    """The child's answer if it is well formed, else `None`."""
    try:
        reply = json.loads(stdout)
        if isinstance(reply, dict):
            ok = reply.get('ok')
            if ok is None and reply.get('error') in _REPLY_ERRORS:
                return reply
            if (
                isinstance(ok, dict)
                and isinstance(ok.get('text'), str)
                and isinstance(ok.get('images'), list)
                and all(isinstance(image, str) for image in ok['images'])
                and isinstance(ok.get('preview_jpeg'), str | None)
            ):
                return reply
    except ValueError:
        pass
    return None


_OOM_SCORE_ADJ_PATH = '/proc/self/oom_score_adj'


def _prefer_to_be_oom_killed(path: str = _OOM_SCORE_ADJ_PATH) -> None:
    """In the child: make the kernel's OOM killer pick this process before the worker's other
    processes (the largest-RSS heuristic could otherwise pick the worker, and the supervisor then
    restarts the whole container). Raising one's own score needs no privilege; not Linux, nothing
    to do."""
    try:
        with open(path, 'w') as score:
            score.write('1000')
    except OSError:
        pass


def _limit_resources(memory_mb: int, cpu_seconds: float) -> None:
    """In the child: cap the address space (Linux; elsewhere `RLIMIT_AS` isn't enforced and macOS
    even rejects it), the CPU time (the soft limit ends the child with SIGXCPU; it also ends a
    child orphaned by a killed worker) and core dumps."""
    import resource

    cpu = max(1, int(cpu_seconds))
    limits = [
        (resource.RLIMIT_CPU, (cpu, cpu + _CPU_GRACE_SECONDS)),
        (resource.RLIMIT_CORE, (0, 0)),
    ]
    if sys.platform.startswith('linux'):
        limit = memory_mb * 1024 * 1024
        limits.append((resource.RLIMIT_AS, (limit, limit)))
    for which, value in limits:
        try:
            resource.setrlimit(which, value)
        except ValueError, OSError:
            pass


def _peak_address_space() -> int | None:
    """The most address space this process has had mapped, in bytes (Linux; else `None`)."""
    try:
        with open('/proc/self/status') as status:
            for line in status:
                if line.startswith('VmPeak:'):
                    return int(line.split()[1]) * 1024
    except OSError, ValueError, IndexError:
        pass
    return None


#: A pdfium failure after the child had mapped this fraction of its budget is reported as the
#: budget's (see `_RenderFailedError`). High on purpose: a corrupt but large image or PDF can map
#: a good part of the budget without having run out of it. A known limitation: `VmPeak` counts
#: mappings that succeeded, so one big allocation that fails well below the limit (400 MB asked
#: at 700 of 1024 MB mapped) leaves a peak under this and the file is called unreadable instead
#: of too complex. Both are permanent and refunded; only the label and help text differ.
_NEAR_BUDGET = 0.85


def _handle_request(payload: bytes) -> dict:
    """The child's work: `payload` is the request line and the file; returns the reply."""
    header, _, data = payload.partition(b'\n')
    return _reply_for(data, json.loads(header))


def _reply_for(data: bytes, request: dict) -> dict:
    try:
        prepared = _prepare_guarded(
            data, request['content_type'], max_pdf_pages=request['max_pdf_pages']
        )
    except UnsupportedDocumentError as exc:
        return {'error': 'unsupported', 'message': str(exc)}
    except DocumentTooComplexError as exc:
        return {'error': 'too_complex', 'message': str(exc)}
    except _RenderFailedError as exc:
        # Hitting `RLIMIT_AS` mostly shows up as a `MemoryError`, but pdfium turns a failed
        # allocation into its own error, which is not the file being corrupt.
        peak = _peak_address_space()
        if peak is not None and peak >= _NEAR_BUDGET * request['memory_mb'] * 1024 * 1024:
            return {'error': 'too_complex', 'message': _OVER_MEMORY}
        return {'error': 'unreadable', 'message': str(exc)}
    except UnreadableDocumentError as exc:
        return {'error': 'unreadable', 'message': str(exc)}
    except DocumentChildError as exc:
        return {'error': 'failed', 'message': str(exc)}
    return {
        'ok': {
            'text': prepared.text,
            'images': [base64.b64encode(image).decode('ascii') for image in prepared.images],
            'preview_jpeg': (
                base64.b64encode(prepared.preview_jpeg).decode('ascii')
                if prepared.preview_jpeg is not None
                else None
            ),
        }
    }


def _child_main() -> None:
    _prefer_to_be_oom_killed()
    # Libraries log and warn about the file (pypdf prints one line per bad object, and can echo
    # strings from it); the parent drops stderr anyway, this keeps the child from spending time on it.
    logging.disable(logging.CRITICAL)
    warnings.simplefilter('ignore')
    # Keep anything a library prints to stdout out of the reply.
    reply_stream = os.fdopen(os.dup(1), 'wb')
    os.dup2(2, 1)
    try:
        payload = sys.stdin.buffer.read()
        request = json.loads(payload.partition(b'\n')[0])
        _limit_resources(request['memory_mb'], request['cpu_seconds'])
        reply = _handle_request(payload)
        out = json.dumps(reply).encode()
    except MemoryError:
        out = json.dumps({'error': 'too_complex', 'message': _OVER_MEMORY}).encode()
    except Exception as exc:  # a broken request or installation: nothing to do with the file
        out = json.dumps({'error': 'failed', 'message': type(exc).__name__}).encode()
    reply_stream.write(out)
    reply_stream.flush()


if __name__ == '__main__':
    _child_main()
