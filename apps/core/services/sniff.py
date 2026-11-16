"""Recognising a file's type from its first bytes, for the formats a browser may show inline.

`sniff_inline_type` is deliberately small: it answers only "is this one of the types
`apps.core.services.storage.INLINE_CONTENT_TYPES` lets a presigned URL serve inline?" -- PDF, JPEG,
PNG, GIF and WebP -- and by magic bytes alone, never the file name or the type the client claimed.
Anything else (an executable named `photo.png`, an HTML or SVG file, plain text) is `None`, and the
caller then serves the file as a download. (Receipts has its own, wider sniffer for the documents it
reads; this one is for products that merely store files.)

`sniff_media_type` (video and audio) and `looks_like_text` came with file transfer's previews
(issue #311); they are separate functions so To-do's attachments, which use `sniff_inline_type`,
keep serving only PDF and images inline.
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


#: `ftyp` brands of an MP4 container a browser plays as video, and of an audio-only one (M4A).
#: Deliberately lists, not "any `ftyp`": HEIC, AVIF and QuickTime files share the box.
_MP4_VIDEO_BRANDS = frozenset(
    {
        b'isom',
        b'iso2',
        b'iso3',
        b'iso4',
        b'iso5',
        b'iso6',
        b'mp41',
        b'mp42',
        b'avc1',
        b'dash',
        b'mmp4',
        b'M4V ',
        b'f4v ',
        b'MSNV',
        b'NDAS',
    }
)
_MP4_AUDIO_BRANDS = frozenset({b'M4A ', b'M4B ', b'M4P ', b'F4A '})

#: The second byte of an MPEG audio frame header (after `0xFF`) that is Layer III (MP3), as
#: MPEG-1, MPEG-2 or MPEG-2.5, with or without the CRC bit.
_MP3_FRAME_BYTES = frozenset({0xFB, 0xFA, 0xF3, 0xF2, 0xE3, 0xE2})

_EBML_SIGNATURE = b'\x1a\x45\xdf\xa3'


def sniff_media_type(head: bytes) -> str | None:
    """The video or audio content type `head` starts as, or `None`: MP4 and WebM video, MP3, WAV,
    Ogg and M4A audio -- the formats `<video>` and `<audio>` play in every current browser.

    By magic bytes alone, like `sniff_inline_type`; a Matroska file (same EBML header as WebM) or a
    QuickTime one is `None`. Needs about 64 bytes (`SNIFF_BYTES` is not enough: the WebM
    document type sits after the EBML header).
    """
    if head[4:8] == b'ftyp':
        brand = head[8:12]
        if brand in _MP4_AUDIO_BRANDS:
            return 'audio/mp4'
        if brand in _MP4_VIDEO_BRANDS:
            return 'video/mp4'
        return None
    if head.startswith(_EBML_SIGNATURE):
        return 'video/webm' if b'webm' in head[:64] else None
    if head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        return 'audio/wav'
    if head.startswith(b'OggS'):
        return 'audio/ogg'
    if head.startswith(b'ID3') or (
        len(head) > 1 and head[0] == 0xFF and head[1] in _MP3_FRAME_BYTES
    ):
        return 'audio/mpeg'
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
