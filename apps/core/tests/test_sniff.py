import pytest

from apps.core.services.sniff import looks_like_text, sniff_inline_type, sniff_media_type
from apps.core.services.storage import INLINE_CONTENT_TYPES

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    ('head', 'expected'),
    [
        (b'%PDF-1.7\n%\xe2\xe3', 'application/pdf'),
        (b'\xff\xd8\xff\xe0\x00\x10JFIF', 'image/jpeg'),
        (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR', 'image/png'),
        (b'GIF87a\x01\x00', 'image/gif'),
        (b'GIF89a\x01\x00', 'image/gif'),
        (b'RIFF\x24\x00\x00\x00WEBPVP8 ', 'image/webp'),
    ],
)
def test_recognises_the_inline_types(head, expected):
    assert sniff_inline_type(head) == expected
    assert expected in INLINE_CONTENT_TYPES


@pytest.mark.parametrize(
    'head',
    [
        b'',
        b'MZ\x90\x00\x03\x00\x00\x00',  # a Windows executable
        b'<!DOCTYPE html><script>alert(1)</script>',
        b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        b'hello world\n',
        b' %PDF-1.7',  # not at the very start
        b'RIFF\x24\x00\x00\x00WAVEfmt ',  # RIFF, but not WebP
        b'GIF90a',
        b'\x89PNG',  # a truncated PNG signature
    ],
)
def test_anything_else_is_none(head):
    assert sniff_inline_type(head) is None


@pytest.mark.parametrize(
    ('head', 'expected'),
    [
        (b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00', 'video/mp4'),
        (b'\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00', 'video/mp4'),
        (b'\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00', 'audio/mp4'),
        (b'\x1a\x45\xdf\xa3\x01\x00\x00\x00B\x82\x84webm', 'video/webm'),
        (b'RIFF\x24\x00\x00\x00WAVEfmt ', 'audio/wav'),
        (b'OggS\x00\x02\x00\x00', 'audio/ogg'),
        (b'ID3\x04\x00\x00\x00\x00', 'audio/mpeg'),
        (b'\xff\xfb\x90\x00', 'audio/mpeg'),
    ],
)
def test_recognises_video_and_audio(head, expected):
    assert sniff_media_type(head) == expected
    assert expected in INLINE_CONTENT_TYPES
    # To-do's attachments only ever serve PDF and images inline.
    assert sniff_inline_type(head) is None


@pytest.mark.parametrize(
    'head',
    [
        b'',
        b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00',  # an image in an MP4 box
        b'\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00',  # QuickTime
        b'\x1a\x45\xdf\xa3\x01\x00\x00\x00B\x82\x84matroska',  # Matroska, not WebM
        b'RIFF\x24\x00\x00\x00WEBPVP8 ',
        b'\xff\xd8\xff\xe0',
        b'<html></html>',
        b'\xff\x00\x00',
    ],
)
def test_other_things_are_not_media(head):
    assert sniff_media_type(head) is None


@pytest.mark.parametrize(
    'head',
    [
        b'hello world\n',
        'café, naïve 日本語\n'.encode(),
        b'\xef\xbb\xbfwith a byte order mark',
        b'a,b,c\r\n1,2,3\r\n\tx\x0c',
        b'<!DOCTYPE html><script>alert(1)</script>',  # text, which a caller shows inert
        'abcé'.encode()[:-1],  # a multi-byte character cut at the end
    ],
)
def test_looks_like_text(head):
    assert looks_like_text(head)


@pytest.mark.parametrize(
    'head',
    [
        b'',
        b'\x00',
        b'abc\x00def',
        b'\x89PNG\r\n\x1a\n',
        b'\xff\xfe a\x00b\x00',  # UTF-16
        b'\xc3\x28 invalid utf-8',
        b'bell\x07',
        b'escape\x1b[0m',
        b'del\x7f',
    ],
)
def test_does_not_look_like_text(head):
    assert not looks_like_text(head)
