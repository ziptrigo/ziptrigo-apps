"""Renaming a stored file while keeping its extension.

The products show a file's name and use it as the download name. Renaming one edits the name
*without* its extension, which stays: `rename('2026-11-21-10-45-30.webp', 'Whiteboard')` is
`Whiteboard.webp`. A page shows the name and its extension apart (`split_extension`), the box
holding only the first.

An extension is a final `.` followed by 1 to 10 ASCII letters or digits, after at least one other
character, so `.env`, `README` and `Report v1.2 final` have none (the whole name is edited). A new
name that already ends in the file's extension (someone typed `photo.webp`) does not get it twice;
any other ending is part of the name (`photo.png` for a `.webp` is `photo.png.webp`: the bytes are
still a WebP).

The new name is cleaned the way every product cleans a file name: control and format characters
(zero-width spaces, bidi overrides) dropped, except a zero-width joiner between two characters (it
holds emoji sequences such as 👨‍👩‍👧 together), `/` and `\\` turned into `_` (a name is never a path), white
space collapsed, and cut so that the name and its extension fit in `max_length`. Each product still
applies its own rules on top (a reserved name, its own length).
"""

import re
import unicodedata

EXTENSION_RE = re.compile(r'\.[A-Za-z0-9]{1,10}$')

#: What a product says when the new name is empty after cleaning.
EMPTY_NAME_MESSAGE = 'Give the file a name.'

_WHITESPACE = re.compile(r'\s+')


def split_extension(name: str) -> tuple[str, str]:
    """`(stem, extension)`: `('report', '.pdf')`, `('README', '')`, `('.env', '')`."""
    match = EXTENSION_RE.search(name)
    if match is None or match.start() == 0:
        return name, ''
    return name[: match.start()], match.group()


#: The zero-width joiner: a format character, but the glue of emoji sequences (a family, a
#: profession with a skin tone), so it is kept between two other characters.
ZWJ = '\u200d'


def clean_stem(value: str) -> str:
    """The typed name, cleaned (see the module docstring); `''` when nothing is left."""
    kept = ''.join(ch for ch in value if ch == ZWJ or unicodedata.category(ch) not in ('Cc', 'Cf'))
    kept = kept.replace('/', '_').replace('\\', '_')
    # A joiner that joins nothing (at an end, next to a space or another joiner) is dropped.
    kept = re.sub(f'(?<![^\\s{ZWJ}]){ZWJ}|{ZWJ}(?![^\\s{ZWJ}])', '', kept)
    return _WHITESPACE.sub(' ', kept).strip()


def renamed(current: str, new_name: str, *, max_length: int = 255) -> str:
    """`current` with its name replaced by `new_name` and its extension kept. `''` when the new
    name is empty after cleaning (the caller refuses it with `EMPTY_NAME_MESSAGE`)."""
    _, extension = split_extension(current)
    stem = clean_stem(new_name)
    if extension and stem.lower().endswith(extension.lower()) and len(stem) > len(extension):
        stem = stem[: -len(extension)].rstrip()
    stem = stem[: max(max_length - len(extension), 0)].rstrip()
    if not stem:
        return ''
    return stem + extension
