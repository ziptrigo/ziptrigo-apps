"""Recognising a file's type from its first bytes, for the formats a browser may show inline.

`sniff_inline_type` is deliberately small: it answers only "is this one of the types
`apps.core.services.storage.INLINE_CONTENT_TYPES` lets a presigned URL serve inline?" -- PDF, JPEG,
PNG, GIF and WebP -- and by magic bytes alone, never the file name or the type the client claimed.
Anything else (an executable named `photo.png`, an HTML or SVG file, plain text) is `None`, and the
caller then serves the file as a download. (Receipts has its own, wider sniffer for the documents it
reads; this one is for products that merely store files.)
"""

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
