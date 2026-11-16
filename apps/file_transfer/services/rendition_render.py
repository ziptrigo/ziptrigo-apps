"""Reducing an image or a PDF to small WebP pages, for the download page's previews (issue #311).

This file is **both** a library (`render`, called in-process by the tests and by the parent's
fallback) **and the script the sandboxed child runs** (`python -I rendition_render.py`): importing
`apps.file_transfer.services.rendition_render` would import the whole Django-dependent services
package, so this file has no relative imports and needs nothing of the project -- only Pillow and
`pypdfium2`, imported where used. The parent side (the child's budget, how its death is read, the
state machine, storage) is `services.renditions`; the pattern is Receipts' document preparation
(`apps.receipts.services.documents`, whose docstring explains the budget at length: products may
not import each other, so this is a small copy of the minimal protocol, not a shared runner).

**What is produced.** An image (JPEG, PNG, WebP, GIF: the first frame) becomes **one** WebP, its
longer side at most `MAX_SIDE_PX` (never enlarged), EXIF orientation applied, every kind of
metadata dropped (EXIF, XMP and the ICC profile are never passed to the encoder). A PDF becomes one
WebP per page, the first `MAX_PREVIEW_PAGES` pages only, each rendered at a scale that keeps its
longer side within `MAX_SIDE_PX` (and at most `PDF_MAX_SCALE`, 144 dpi). An image whose header
claims more than `MAX_IMAGE_PIXELS` is refused **before** its pixels are decoded; a PDF page of
absurd size is refused.

**Two kinds of failure.** `UnreadableError`: the file is corrupt, encrypted or nothing we can
read. `TooComplexError`: it is over a hard limit (pixels, page size) or needs more memory than the
child's budget. Both are verdicts about the file (the parent records it as unavailable); anything
that says nothing about the file (the child dying of an outside signal, a timeout) is the
parent's `RenditionFailed`, which is retried by the next click.

**Protocol.** The parent writes one JSON line (`kind`, `max_pages`, `memory_mb`, `cpu_seconds`)
and then the file's bytes to the child's stdin. Once its limits are in force the child writes
`STARTED_MARKER`, and then one JSON document on stdout: `{"ok": {"pages": [base64 WebP, ...],
"total_pages": n}}` or `{"error": "unreadable" | "too_complex" | "failed", "message": ...}`.
"""

import base64
import io
import json
import logging
import os
import sys
import warnings

from PIL import Image, ImageOps

#: Longest side, in pixels, of a rendition (an image is never enlarged to reach it).
MAX_SIDE_PX = 1600
WEBP_QUALITY = 75
#: An image whose header claims more pixels than this is not reduced (checked before decoding).
MAX_IMAGE_PIXELS = 100_000_000
#: How many pages of a PDF are rendered.
MAX_PREVIEW_PAGES = 20
#: `pypdfium2` render scale: 1 is 72 dpi, so 2 is 144 dpi.
PDF_MAX_SCALE = 2.0
#: A PDF page bigger than this on a side, in points (the PDF spec's own maximum, 200 inches).
MAX_PDF_PAGE_POINTS = 14_400

IMAGE = 'image'
PDF = 'pdf'

#: The first thing the child writes, once its limits are set: tells a child that ran out of memory
#: while working from one that never got going.
STARTED_MARKER = b'#started\n'

_OVER_MEMORY = 'The file needs more memory than allowed.'


class RenderError(Exception):
    """Base class of the file-is-the-problem errors."""


class UnreadableError(RenderError):
    """Corrupt, encrypted, or not something this can read."""


class TooComplexError(RenderError):
    """Over a hard limit, or needs more memory or time than the budget allows."""


def _encode_webp(image: Image.Image) -> bytes:
    """Shrink, orient and encode `image` as a WebP with no metadata."""
    if image.mode in ('1', 'P', 'PA'):
        # A bilevel or palette image can only be resampled with NEAREST: cut it to at most twice
        # the target that way first, so the expansion below is small whatever its size.
        image.thumbnail((2 * MAX_SIDE_PX, 2 * MAX_SIDE_PX), Image.Resampling.NEAREST)
        has_alpha = image.mode == 'PA' or 'transparency' in image.info
        image = image.convert('RGBA' if has_alpha else 'RGB')
    # In place, before the rotation and conversions below copy the bitmap (`thumbnail` never
    # enlarges, and keeps `info`, so the EXIF orientation survives for the next line).
    image.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.Resampling.LANCZOS)
    image = ImageOps.exif_transpose(image) or image
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
    out = io.BytesIO()
    # No `exif`, `xmp` or `icc_profile` argument: nothing about the original travels.
    image.save(out, format='WEBP', quality=WEBP_QUALITY, method=4)
    return out.getvalue()


def render_image(data: bytes) -> list[bytes]:
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            image = Image.open(io.BytesIO(data))
            if image.format not in ('JPEG', 'PNG', 'WEBP', 'GIF'):
                raise UnreadableError('This kind of image is not supported.')
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise TooComplexError('The image is too large to reduce.')
            if image.format == 'JPEG':
                # Lets libjpeg decode at a reduced size, which keeps a big photo's memory down.
                image.draft('RGB', (MAX_SIDE_PX, MAX_SIDE_PX))
            image.load()
            return [_encode_webp(image)]
        except RenderError:
            raise
        except Image.DecompressionBombError as exc:
            raise TooComplexError('The image is too large to reduce.') from exc
        except MemoryError:
            raise
        except Exception as exc:  # Pillow raises a wide variety of errors on damaged files
            raise UnreadableError(f'The image could not be read: {type(exc).__name__}') from exc


def _scale(width_pt: float, height_pt: float) -> float:
    if not (0 < width_pt <= MAX_PDF_PAGE_POINTS and 0 < height_pt <= MAX_PDF_PAGE_POINTS):
        raise TooComplexError('The PDF page size is not supported.')
    return min(PDF_MAX_SCALE, MAX_SIDE_PX / max(width_pt, height_pt))


def render_pdf(data: bytes, max_pages: int = MAX_PREVIEW_PAGES) -> tuple[list[bytes], int]:
    """`(the WebP pages, the PDF's real page count)`."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:  # corrupt, encrypted, or pdfium out of memory
        raise UnreadableError(f'The PDF could not be opened: {type(exc).__name__}') from exc
    try:
        total = len(pdf)
        if total < 1:
            raise UnreadableError('The PDF has no pages.')
        pages: list[bytes] = []
        for index in range(min(total, max(1, max_pages))):
            page = pdf[index]
            try:
                width_pt, height_pt = page.get_size()
                bitmap = page.render(scale=_scale(width_pt, height_pt))
                pages.append(_encode_webp(bitmap.to_pil()))
            finally:
                page.close()
        return pages, total
    except RenderError:
        raise
    except MemoryError:
        raise
    except Exception as exc:
        raise UnreadableError(f'The PDF could not be rendered: {type(exc).__name__}') from exc
    finally:
        pdf.close()


def render(kind: str, data: bytes, max_pages: int = MAX_PREVIEW_PAGES) -> tuple[list[bytes], int]:
    """`(pages, total page count)` of `data`, an image or a PDF by `kind`. Raises
    `UnreadableError` or `TooComplexError`; a `MemoryError` is the caller's to map (it means the
    file is over the memory budget)."""
    if kind == IMAGE:
        return render_image(data), 1
    if kind == PDF:
        return render_pdf(data, max_pages)
    raise UnreadableError('This kind of file is not supported.')


# -- The child process --------------------------------------------------------------------------

_CPU_GRACE_SECONDS = 2
_OOM_SCORE_ADJ_PATH = '/proc/self/oom_score_adj'


def _prefer_to_be_oom_killed(path: str = _OOM_SCORE_ADJ_PATH) -> None:
    """Make the kernel's OOM killer pick this process before the worker (raising one's own score
    needs no privilege; not Linux, nothing to do)."""
    try:
        with open(path, 'w') as score:
            score.write('1000')
    except OSError:
        pass


def _limit_resources(memory_mb: int, cpu_seconds: float) -> None:
    """Cap the CPU time (SIGXCPU ends the child), core dumps and, on Linux, the address space
    (elsewhere `RLIMIT_AS` isn't enforced, and macOS rejects it)."""
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


def _reply_for(data: bytes, request: dict) -> dict:
    try:
        pages, total = render(request['kind'], data, request['max_pages'])
    except TooComplexError as exc:
        return {'error': 'too_complex', 'message': str(exc)}
    except UnreadableError as exc:
        return {'error': 'unreadable', 'message': str(exc)}
    return {
        'ok': {
            'pages': [base64.b64encode(page).decode('ascii') for page in pages],
            'total_pages': total,
        }
    }


def _child_main() -> None:
    _prefer_to_be_oom_killed()
    # Libraries log and warn about the file; the parent drops stderr anyway.
    logging.disable(logging.CRITICAL)
    warnings.simplefilter('ignore')
    # Keep anything a library prints to stdout out of the reply.
    reply_stream = os.fdopen(os.dup(1), 'wb')
    os.dup2(2, 1)
    try:
        payload = sys.stdin.buffer.read()
        header, _, data = payload.partition(b'\n')
        request = json.loads(header)
        _limit_resources(request['memory_mb'], request['cpu_seconds'])
        reply_stream.write(STARTED_MARKER)
        reply_stream.flush()
        out = json.dumps(_reply_for(data, request)).encode()
    except MemoryError:
        out = json.dumps({'error': 'too_complex', 'message': _OVER_MEMORY}).encode()
    except Exception as exc:  # a broken request or installation: nothing to do with the file
        out = json.dumps({'error': 'failed', 'message': type(exc).__name__}).encode()
    reply_stream.write(out)
    reply_stream.flush()


if __name__ == '__main__':
    _child_main()
