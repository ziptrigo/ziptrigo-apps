"""File previews on the public download page (issue #311): which files can be shown before they
are downloaded, and how.

**The type is read from the file's bytes, never from its name or the uploader's claimed type.**
`detect_preview_type` runs when an upload completes (`uploads.finish_upload_in_storage`: one ranged
`GetObject` of the first `SNIFF_BYTES`), and the result is stored on `TransferFile.preview_type`;
everything afterwards (the page's buttons, the preview endpoints) reads only that column. So an
HTML file renamed `photo.png` is never previewed as an image: it sniffs as text, and text is shown
as inert text (below).

**A preview is a sample, never the file** (the owner's redesign): previewing must not be a way
around a download limit, so what a preview shows is something smaller than the file.

- `image` (JPEG, PNG, WebP, GIF) and `pdf`: a downsized WebP -- one image, or one per page of a
  PDF -- built lazily on the first click and reused (`services.renditions`). The file's own bytes
  are never served by a preview endpoint.
- `markdown` and `text`: the **first `TEXT_PREVIEW_BYTES`** (64 KB) of the file. A text file is
  anything that is valid UTF-8 without control characters in its first chunk; one whose **name**
  ends in `.md` or `.markdown` is Markdown (the name only chooses between formatted and plain among
  files whose bytes already say "text"). The endpoint answers the text as `text/plain` (never from
  S3, never HTML: `nosniff` and a sandboxing CSP) or, for Markdown, JSON with the raw text and the
  text rendered by `apps.core.markdown.render` (`extended` profile, no task references: raw HTML
  is escaped, images are never rendered, the output is sanitised). The decoded sample / rendered
  JSON is cached per file id for `SAMPLE_CACHE_SECONDS` (a file never changes). An SVG or HTML
  file is text too, so its source is shown, not rendered.

Video and audio have **no preview** (playing them is downloading them, and there is nothing cheap
to downsize).

Seeing the whole file is "View full image" / "View full document", which **counts as a download**
(`views.download.preview_full`, `services.downloads`). For text it is offered only for a file the
sample cut and that is at most `FULL_TEXT_MAX_BYTES`.

A file uploaded before the column existed has `preview_type == ''` and so no preview, until
`./manage.py file_transfer_backfill_previews` reads its head (the page never sniffs: it would cost
an S3 request per file per page view).
"""

import codecs
import logging
from collections.abc import Iterable
from enum import StrEnum

from django.core.cache import cache
from django.template.defaultfilters import filesizeformat
from django.urls import reverse

from apps.core.markdown import MAX_MARKDOWN_CHARS, render
from apps.core.services.sniff import looks_like_text, sniff_inline_type

from ..models import Transfer, TransferFile
from .storage import S3Storage

logger = logging.getLogger(__name__)

#: How many bytes of the head of an object `detect_preview_type` needs: text is judged on this much.
SNIFF_BYTES = 4096

#: How much of a text file the preview shows (and `preview_file` reads from S3): a sample.
TEXT_PREVIEW_BYTES = 64 * 1024

#: The largest text file "View full document" shows (it is read whole by the browser); a larger
#: one only offers the sample and Download.
FULL_TEXT_MAX_BYTES = 5 * 1024 * 1024

#: How long a decoded text sample / rendered Markdown is kept in the cache (per file id).
SAMPLE_CACHE_SECONDS = 300


#: Names that make a text file Markdown.
MARKDOWN_EXTENSIONS = ('.md', '.markdown')


class PreviewKind(StrEnum):
    IMAGE = 'image'
    PDF = 'pdf'
    TEXT = 'text'
    MARKDOWN = 'markdown'


#: The content types a file may be stored as, and what shows them.
_KINDS: dict[str, PreviewKind] = {
    'image/jpeg': PreviewKind.IMAGE,
    'image/png': PreviewKind.IMAGE,
    'image/webp': PreviewKind.IMAGE,
    'image/gif': PreviewKind.IMAGE,
    'application/pdf': PreviewKind.PDF,
    'text/plain': PreviewKind.TEXT,
    'text/markdown': PreviewKind.MARKDOWN,
}

#: Font Awesome icon of each kind.
_ICONS: dict[PreviewKind, str] = {
    PreviewKind.IMAGE: 'fas fa-file-image',
    PreviewKind.PDF: 'fas fa-file-pdf',
    PreviewKind.TEXT: 'fas fa-file-lines',
    PreviewKind.MARKDOWN: 'fas fa-file-lines',
}

_GENERIC_ICON = 'fas fa-file'

#: Icons by extension for files that have no preview. Cosmetic only (nothing is decided by it).
_EXTENSION_ICONS: dict[str, str] = {
    'zip': 'fas fa-file-zipper',
    'rar': 'fas fa-file-zipper',
    '7z': 'fas fa-file-zipper',
    'gz': 'fas fa-file-zipper',
    'tar': 'fas fa-file-zipper',
    'doc': 'fas fa-file-word',
    'docx': 'fas fa-file-word',
    'xls': 'fas fa-file-excel',
    'xlsx': 'fas fa-file-excel',
    'ppt': 'fas fa-file-powerpoint',
    'pptx': 'fas fa-file-powerpoint',
    'mp4': 'fas fa-file-video',
    'webm': 'fas fa-file-video',
    'mov': 'fas fa-file-video',
    'mkv': 'fas fa-file-video',
    'avi': 'fas fa-file-video',
    'mp3': 'fas fa-file-audio',
    'wav': 'fas fa-file-audio',
    'ogg': 'fas fa-file-audio',
    'm4a': 'fas fa-file-audio',
    'flac': 'fas fa-file-audio',
    'heic': 'fas fa-file-image',
    'svg': 'fas fa-file-image',
    'html': 'fas fa-file-code',
    'py': 'fas fa-file-code',
    'js': 'fas fa-file-code',
}


def detect_preview_type(head: bytes, name: str = '') -> str:
    """The content type a file whose first bytes are `head` may be previewed as, or `''`.

    Order matters: the image and PDF formats first, text last (anything that decodes as plain
    text is text, whatever the file is called). `name` matters only for that last step: a text file
    called `*.md` / `*.markdown` is `text/markdown`, every other text file `text/plain`."""
    return sniff_inline_type(head) or _text_type(head, name)


def _text_type(head: bytes, name: str) -> str:
    if not looks_like_text(head):
        return ''
    return 'text/markdown' if name.lower().endswith(MARKDOWN_EXTENSIONS) else 'text/plain'


def sniff_stored_object(storage: S3Storage, key: str, size: int, name: str = '') -> str:
    """Read the head of the stored object and `detect_preview_type` it. Never raises: a preview
    is a nicety, so a storage hiccup (or an empty object) means `''`, not a failed upload."""
    if size <= 0:
        return ''
    try:
        head = storage.get_object_range(key, 0, min(size, SNIFF_BYTES) - 1)
    except Exception:
        logger.warning('Could not read the head of %s to choose its preview', key, exc_info=True)
        return ''
    return detect_preview_type(head, name)


def render_markdown_preview(raw: bytes) -> dict:
    """The JSON of a Markdown preview: `text` (the sample as UTF-8), `html` (that text rendered,
    sanitised) and `html_truncated` (the formatted view covers only the first `MAX_MARKDOWN_CHARS`
    characters, cut at a line end: the renderer refuses more). If it cannot be rendered at all,
    `html` is `None` and the dialog shows the raw text."""
    text = decode_text(raw)
    source = text
    truncated = len(source) > MAX_MARKDOWN_CHARS
    if truncated:
        source = source[:MAX_MARKDOWN_CHARS]
        source = source[: source.rfind('\n')] if '\n' in source else source
    try:
        html = str(render(source, profile='extended'))
    except Exception:
        logger.warning('Could not render a Markdown preview', exc_info=True)
        html = None
    return {'text': text, 'html': html, 'html_truncated': truncated and html is not None}


def decode_text(raw: bytes) -> str:
    """`raw` as text: UTF-8, a character cut off at the end held back, anything invalid replaced."""
    return codecs.getincrementaldecoder('utf-8')(errors='replace').decode(raw)


def text_sample(file: TransferFile, kind: PreviewKind, storage: S3Storage) -> str | dict:
    """The sample of a text (`str`) or Markdown (`dict`, `render_markdown_preview`) file: its first
    `TEXT_PREVIEW_BYTES`, decoded (and rendered). **Cached per file id** for `SAMPLE_CACHE_SECONDS`
    -- a file never changes, so a visitor flipping through files and back costs one S3 read and one
    render, not one per view. The caller has already applied every gate: the cache is not one."""
    key = f'ft:preview-sample:{kind.value}:{file.pk}'
    cached = cache.get(key)
    if cached is not None:
        return cached
    raw = storage.get_object_range(file.storage_key, 0, TEXT_PREVIEW_BYTES - 1)
    sample: str | dict = (
        render_markdown_preview(raw) if kind == PreviewKind.MARKDOWN else decode_text(raw)
    )
    cache.set(key, sample, SAMPLE_CACHE_SECONDS)
    return sample


def kind_of(preview_type: str) -> PreviewKind | None:
    """What shows a file stored as `preview_type`, or `None` when it has no preview."""
    return _KINDS.get(preview_type)


def is_sample_cut(file: TransferFile) -> bool:
    """Whether a text file is longer than the sample the preview shows."""
    return file.size > TEXT_PREVIEW_BYTES


def full_label(file: TransferFile, kind: PreviewKind) -> str:
    """The text of the dialog's "view the whole file" button -- which counts as a download -- or
    `''` when there is none: an image's, a PDF's, and a text file's only when the sample cut it and
    the file is small enough to show whole."""
    if kind == PreviewKind.IMAGE:
        return 'View full image'
    if kind == PreviewKind.PDF:
        return 'View full document'
    if is_sample_cut(file) and file.size <= FULL_TEXT_MAX_BYTES:
        return 'View full document'
    return ''


def icon_for(name: str, preview_type: str) -> str:
    """The icon of a file row: by kind when it can be previewed, else by extension."""
    kind = kind_of(preview_type)
    if kind is not None:
        return _ICONS[kind]
    extension = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return _EXTENSION_ICONS.get(extension, _GENERIC_ICON)


def page_context(transfer: Transfer, files: Iterable[TransferFile], *, unlocked: bool) -> dict:
    """What `download.html` needs about previews, for the files in the order it lists them:

    - `rows`: one `{file, kind, icon, previewable}` per file;
    - `previews`: the previewable files as the page's script wants them (id, name, size, kind,
      icon, the URLs of its preview, of "view full" and of its download, whether the text sample
      was cut) -- **empty while the transfer is locked by a password**, so a locked page carries no
      preview URL at all;
    - `preview_hint` and `preview_count`: the line above the list and how many files can be
      previewed (the hint also appears locked, worded "once you enter the password");
    - `preview_total`: how many files the transfer has.
    """
    rows = []
    previews = []
    for file in files:
        kind = kind_of(file.preview_type)
        rows.append(
            {
                'file': file,
                'kind': kind.value if kind else '',
                'icon': icon_for(file.name, file.preview_type),
                'previewable': kind is not None,
            }
        )
        if kind is not None and unlocked:
            is_text = kind in (PreviewKind.TEXT, PreviewKind.MARKDOWN)
            label = full_label(file, kind)
            args = [transfer.slug, file.id]
            previews.append(
                {
                    'id': str(file.id),
                    'name': file.name,
                    'size': filesizeformat(file.size),
                    'kind': kind.value,
                    'icon': icon_for(file.name, file.preview_type),
                    # Text: the sample itself. Image and PDF: the build's status (the pages' URLs
                    # come back from it).
                    'url': reverse('t:preview' if is_text else 't:preview-status', args=args),
                    'download': reverse('t:download-file', args=args),
                    'full_url': reverse('t:preview-full', args=args) if label else '',
                    'full_label': label,
                    # A text sample the whole file doesn't fit in, with no way to show the rest
                    # here: the dialog says to download it.
                    'sample_cut': is_text and is_sample_cut(file),
                }
            )
    count = sum(1 for row in rows if row['previewable'])
    return {
        'rows': rows,
        'previews': previews,
        'preview_count': count,
        'preview_total': len(rows),
        'preview_hint': hint_text(count, len(rows), unlocked=unlocked),
    }


def hint_text(previewable: int, total: int, *, unlocked: bool) -> str:
    """The line above the file list that says previews exist (`''` when none can be)."""
    if previewable <= 0:
        return ''
    when = 'before you download it' if unlocked else 'once you enter the password'
    if total == 1:
        return f'You can preview this file {when}.'
    if not unlocked:
        return f'{previewable} of {total} files can be previewed {when}.'
    if previewable == total:
        return f'All {total} files can be previewed before you download them.'
    if previewable == 1:
        return f'1 of {total} files can be previewed before you download it.'
    return f'{previewable} of {total} files can be previewed before you download them.'


def backfill_previews(*, storage: S3Storage | None = None, batch: int = 200) -> tuple[int, int]:
    """Choose a preview type for files uploaded before the column existed (and any whose sniff
    failed): reads the head of every uploaded, still-stored file with no type, oldest first.
    Returns `(files looked at, files that can now be previewed)`. Idempotent; a file with no
    preview stays `''` and is looked at again by the next run."""
    from ..models import TransferStatus
    from .storage import get_storage

    storage = storage or get_storage()
    live = [TransferStatus.ACTIVE, TransferStatus.DISABLED, TransferStatus.SUSPENDED]
    looked = found = 0
    last_pk = None
    while True:
        queryset = TransferFile.objects.filter(
            uploaded=True, preview_type='', size__gt=0, transfer__status__in=live
        ).order_by('pk')
        if last_pk is not None:
            queryset = queryset.filter(pk__gt=last_pk)
        files = list(queryset[:batch])
        if not files:
            return looked, found
        for file in files:
            looked += 1
            last_pk = file.pk
            file_type = sniff_stored_object(storage, file.storage_key, file.size, file.name)
            if file_type:
                TransferFile.objects.filter(pk=file.pk, preview_type='').update(
                    preview_type=file_type
                )
                found += 1
