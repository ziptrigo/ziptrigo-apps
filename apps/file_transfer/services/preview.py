"""File previews on the public download page (issue #311): which files can be shown before they
are downloaded, and how.

**The type is read from the file's bytes, never from its name or the uploader's claimed type.**
`detect_preview_type` runs when an upload completes (`uploads.finish_upload_in_storage`: one ranged
`GetObject` of the first `SNIFF_BYTES`), and the result is stored on `TransferFile.preview_type`;
everything afterwards (the page's buttons, the preview endpoint) reads only that column. So an HTML
file renamed `photo.png` is never previewed as an image: it sniffs as text, and text is shown as
inert text (below).

What can be previewed, by `PreviewKind`:

- `image`: JPEG, PNG, WebP, GIF, shown in an `<img>`.
- `pdf`: shown by the browser's PDF viewer in an `<iframe>`.
- `video`: MP4 and WebM, in `<video controls>`.
- `audio`: MP3, WAV, Ogg and M4A, in `<audio controls>`.
- `markdown`: a text file (the same check as `text`) whose **name** ends in `.md` or `.markdown`.
  The name only chooses between formatted and plain among files whose bytes already say "text"
  (it can never make a binary file previewable). The preview endpoint answers JSON with the raw
  text and the text rendered by `apps.core.markdown.render` (`extended` profile, no task
  references: raw HTML is escaped, images are never rendered, the output is sanitised); the dialog
  has a Formatted / Raw switch.
- `text`: anything that is valid UTF-8 without control characters in its first chunk and is not one
  of the above. It is **never** served as HTML: `views.download.preview_file` reads the first
  `TEXT_PREVIEW_BYTES` itself and answers `text/plain; charset=utf-8` with `nosniff` and a
  sandboxing CSP, and the page puts it in the dialog with `textContent`. (An SVG or HTML file is
  text too, so its source is shown, not rendered, and it never gets a Formatted view.)

HTML and SVG as images or pages are never inline. `S3Storage.presigned_get_url` independently
refuses to serve a content type outside `INLINE_CONTENT_TYPES` inline.

A file uploaded before the column existed has `preview_type == ''` and so no preview, until
`./manage.py file_transfer_backfill_previews` reads its head (the page never sniffs: it would cost
an S3 request per file per page view).
"""

import codecs
import logging
from collections.abc import Iterable
from enum import StrEnum

from django.template.defaultfilters import filesizeformat
from django.urls import reverse

from apps.core.markdown import MAX_MARKDOWN_CHARS, render
from apps.core.services.sniff import looks_like_text, sniff_inline_type, sniff_media_type

from ..models import Transfer, TransferFile
from .storage import S3Storage

logger = logging.getLogger(__name__)

#: How many bytes of the head of an object `detect_preview_type` needs: the WebM document type
#: sits after the EBML header, and text is judged on this much.
SNIFF_BYTES = 4096

#: How much of a text file the preview shows (and `preview_file` reads from S3).
TEXT_PREVIEW_BYTES = 1024 * 1024


#: Names that make a text file Markdown.
MARKDOWN_EXTENSIONS = ('.md', '.markdown')


class PreviewKind(StrEnum):
    IMAGE = 'image'
    PDF = 'pdf'
    TEXT = 'text'
    MARKDOWN = 'markdown'
    VIDEO = 'video'
    AUDIO = 'audio'


#: The content types a file may be stored as, and what shows them.
_KINDS: dict[str, PreviewKind] = {
    'image/jpeg': PreviewKind.IMAGE,
    'image/png': PreviewKind.IMAGE,
    'image/webp': PreviewKind.IMAGE,
    'image/gif': PreviewKind.IMAGE,
    'application/pdf': PreviewKind.PDF,
    'text/plain': PreviewKind.TEXT,
    'text/markdown': PreviewKind.MARKDOWN,
    'video/mp4': PreviewKind.VIDEO,
    'video/webm': PreviewKind.VIDEO,
    'audio/mpeg': PreviewKind.AUDIO,
    'audio/wav': PreviewKind.AUDIO,
    'audio/ogg': PreviewKind.AUDIO,
    'audio/mp4': PreviewKind.AUDIO,
}

#: Font Awesome icon of each kind.
_ICONS: dict[PreviewKind, str] = {
    PreviewKind.IMAGE: 'fas fa-file-image',
    PreviewKind.PDF: 'fas fa-file-pdf',
    PreviewKind.TEXT: 'fas fa-file-lines',
    PreviewKind.MARKDOWN: 'fas fa-file-lines',
    PreviewKind.VIDEO: 'fas fa-file-video',
    PreviewKind.AUDIO: 'fas fa-file-audio',
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
    'mov': 'fas fa-file-video',
    'mkv': 'fas fa-file-video',
    'avi': 'fas fa-file-video',
    'flac': 'fas fa-file-audio',
    'heic': 'fas fa-file-image',
    'svg': 'fas fa-file-image',
    'html': 'fas fa-file-code',
    'py': 'fas fa-file-code',
    'js': 'fas fa-file-code',
}


def detect_preview_type(head: bytes, name: str = '') -> str:
    """The content type a file whose first bytes are `head` may be previewed as, or `''`.

    Order matters: the specific binary formats first, text last (anything that decodes as plain
    text is text, whatever the file is called). `name` matters only for that last step: a text file
    called `*.md` / `*.markdown` is `text/markdown`, every other text file `text/plain`."""
    return sniff_inline_type(head) or sniff_media_type(head) or _text_type(head, name)


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
    """The JSON of a Markdown preview: `text` (the first `TEXT_PREVIEW_BYTES` as UTF-8), `html`
    (that text rendered, sanitised) and `html_truncated` (the formatted view covers only the first
    `MAX_MARKDOWN_CHARS` characters, cut at a line end: the renderer refuses more). If it cannot
    be rendered at all, `html` is `None` and the dialog shows the raw text."""
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


def kind_of(preview_type: str) -> PreviewKind | None:
    """What shows a file stored as `preview_type`, or `None` when it has no preview."""
    return _KINDS.get(preview_type)


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
      icon, the preview and the download URLs) -- **empty while the transfer is locked by a
      password**, so a locked page carries no preview URL at all;
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
            previews.append(
                {
                    'id': str(file.id),
                    'name': file.name,
                    'size': filesizeformat(file.size),
                    'kind': kind.value,
                    'icon': icon_for(file.name, file.preview_type),
                    'url': reverse('t:preview', args=[transfer.slug, file.id]),
                    'download': reverse('t:download-file', args=[transfer.slug, file.id]),
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
