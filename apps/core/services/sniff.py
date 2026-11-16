"""Recognising a file's type from its first bytes, for the formats a browser may show inline.

`sniff_inline_type` is deliberately small: it answers only "is this one of the types
`apps.core.services.storage.INLINE_CONTENT_TYPES` lets a presigned URL serve inline?" -- PDF, JPEG,
PNG, GIF and WebP -- and by magic bytes alone, never the file name or the type the client claimed.
Anything else (an executable named `photo.png`, an HTML or SVG file, plain text) is `None`, and the
caller then serves the file as a download. (Receipts has its own, wider sniffer for the documents it
reads; this one is for products that merely store files.)

`looks_like_text` came with file transfer's previews (issue #311); it is a separate function so
To-do's attachments, which use `sniff_inline_type`, keep serving only PDF and images inline.
"""

import codecs

#: The most bytes `sniff_inline_type` looks at; a caller needs to keep just this much of an object.
SNIFF_BYTES = 12

_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


def sniff_inline_type(head: bytes) -> str | None:
    """The inline-safe content type `head` (the file's first bytes) starts as, or `None`."""
    if head.startswith(b'%PDF-'):
        return 'application/pdf'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith(_PNG_SIGNATURE):
        return 'image/png'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


#: Control characters a text file may contain besides printable ones: tab, line feed, form feed,
#: carriage return.
_TEXT_CONTROLS = frozenset({0x09, 0x0A, 0x0C, 0x0D})


def looks_like_text(head: bytes) -> bool:
    """Whether `head` (the first bytes of a file) is plain text: valid UTF-8 (a character cut off
    at the very end is fine) with no NUL or other control characters but tab, line feeds and
    carriage returns. A UTF-8 byte order mark is allowed; UTF-16 is not (it is full of NULs), and
    neither is an empty `head`.

    This says nothing about *which* text it is: an HTML or SVG file is text too, which is why a
    caller must show it as inert text (`text/plain`, `textContent`), never as a page.
    """
    if not head:
        return False
    data = head.removeprefix(b'\xef\xbb\xbf')
    try:
        # `final=False`: the cut at the end of `head` may fall inside a multi-byte character.
        text = codecs.getincrementaldecoder('utf-8')().decode(data, final=False)
    except UnicodeDecodeError:
        return False
    for char in text:
        code = ord(char)
        if code < 0x20 and code not in _TEXT_CONTROLS:
            return False
        if code == 0x7F:
            return False
    return True
