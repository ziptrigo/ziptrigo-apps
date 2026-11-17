"""The app-independent half of planning an export (issue #315, moved here from Receipts for the
To-do export, issue #326): how a name becomes a path component, how names are kept unique, how
files are packed into zips and what a zip is called, and the spreadsheet formula guard. Pure
functions: no database, no storage.

- *Names.* A vendor, a project, a task title, a file name or a year becomes one path component
  every platform accepts: path separators, control and format characters (zero-width, bidi
  overrides) and the characters Windows refuses are replaced or dropped, leading dots and trailing
  dots and spaces removed, Windows' reserved names (`CON`, `NUL`, `COM1`...) avoided, and the
  length cut. Two folders whose names differ only by case (macOS and Windows would merge them)
  share one (`Folders`), and two files with the same name in a folder become `name (1).pdf`,
  compared ignoring case (`unique_path`), across every zip, so extracting all the zips into one
  folder merges cleanly.
- *Parts.* Items are packed in the order given; a zip is closed when the next file would take it
  over the part size (a file is never cut in two; a file bigger than the part size goes alone).
- *Cells.* `cell_text` is the formula guard every text written to a CSV or spreadsheet goes
  through.
"""

import re
import unicodedata
from collections.abc import Iterable
from datetime import date

#: Longest folder name and file name, in characters. Windows' whole-path limit is 260; a path here
#: is a few folders and the file name.
FOLDER_NAME_MAX = 80
FILE_NAME_MAX = 120

_REFUSED = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {
    'CON',
    'PRN',
    'AUX',
    'NUL',
    *(f'COM{n}' for n in range(1, 10)),
    *(f'LPT{n}' for n in range(1, 10)),
}


def _strip_ends(text: str) -> str:
    return text.strip(' ').lstrip('.').rstrip('. ')


def _dropped(char: str) -> bool:
    """Characters that are removed outright: format characters (zero-width, bidi overrides),
    surrogates and private use."""
    return unicodedata.category(char) in ('Cf', 'Cs', 'Co')


def _clean(text: str) -> str:
    text = unicodedata.normalize('NFC', text)
    text = ''.join(char for char in text if not _dropped(char))
    text = re.sub(r'\s+', ' ', text)  # a newline or tab is a space, not an "illegal" character
    return _strip_ends(_REFUSED.sub('_', text))


def _avoid_reserved(stem: str) -> str:
    # Windows treats `CON.txt` like `CON`: the part before the first dot counts.
    return f'_{stem}' if stem.split('.', 1)[0].upper() in _RESERVED else stem


def clean_folder_name(name: str, fallback: str) -> str:
    """`name` as one folder name that every platform accepts (`fallback` when nothing is left)."""
    cleaned = _clean(name)[:FOLDER_NAME_MAX]
    cleaned = _strip_ends(cleaned)
    return _avoid_reserved(cleaned) if cleaned else fallback


def clean_file_name(name: str, fallback: str = 'file') -> str:
    """`name` as one file name, keeping a short extension when it has one."""
    cleaned = _clean(name)
    stem, dot, ext = cleaned.rpartition('.')
    if not dot or not stem or len(ext) > 10 or ' ' in ext:
        stem, ext = cleaned, ''
    else:
        ext = f'.{ext}'
    stem = _strip_ends(stem[: max(FILE_NAME_MAX - len(ext), 1)])
    if not stem:
        stem = fallback
    return _avoid_reserved(stem) + ext


def split_extension(name: str) -> tuple[str, str]:
    """`('report', '.pdf')`; no extension (or a dot-file, or something that isn't one) is `(name, '')`."""
    stem, dot, ext = name.rpartition('.')
    if not dot or not stem or len(ext) > 10 or ' ' in ext:
        return name, ''
    return stem, f'.{ext}'


class Folders:
    """Folder names, case-insensitively unique: the first spelling of a folder wins."""

    def __init__(self) -> None:
        self._spelling: dict[str, str] = {}

    def resolve(self, parent: str, name: str) -> str:
        key = f'{parent}/{name}'.casefold()
        if key not in self._spelling:
            self._spelling[key] = name
        return self._spelling[key]


def unique_path(directory: str, name: str, used: set[str]) -> str:
    """`directory/name`, or `directory/stem (1).ext`, `(2)`... when that path is taken, compared
    ignoring case; the path returned is added to `used`."""
    stem, ext = split_extension(name)
    path, n = f'{directory}/{name}', 0
    while path.casefold() in used:
        n += 1
        path = f'{directory}/{stem} ({n}){ext}'
    used.add(path.casefold())
    return path


def pack_parts(sizes: Iterable[int], part_bytes: int) -> list[int]:
    """The part number (from 1) of each size, in order: a part is closed when the next file would
    take it over `part_bytes`, and a file bigger than that goes alone."""
    part, filled = 1, 0
    numbers: list[int] = []
    for size in sizes:
        if filled and filled + size > part_bytes:
            part, filled = part + 1, 0
        numbers.append(part)
        filled += size
    return numbers


def part_file_name(prefix: str, created: date, number: int, count: int) -> str:
    """What a part is downloaded as: `receipts-2026-11-16-part-01-of-05.zip` (no suffix for an
    export that fits one zip)."""
    base = f'{prefix}-{created.isoformat()}'
    return f'{base}.zip' if count <= 1 else f'{base}-part-{number:02d}-of-{count:02d}.zip'


_FORMULA_PREFIXES = ('=', '+', '-', '@')
_CONTROL_PREFIXES = ('\t', '\r')


def cell_text(value: object) -> str:
    """One CSV or spreadsheet cell as text. A text that a spreadsheet would read as a formula (it
    starts with `=`, `+`, `-`, `@`...), even after leading whitespace (some importers trim it), is
    prefixed with an apostrophe: vendors, notes, task titles and comments come from emails and the
    AI, and `=HYPERLINK(...)` in an exported file is a real attack. Every cell goes through here."""
    if value is None:
        return ''
    text = str(value)
    if text.startswith(_CONTROL_PREFIXES) or text.lstrip().startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text
