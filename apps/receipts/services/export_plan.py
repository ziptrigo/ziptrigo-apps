"""Planning an export (issue #315): which folder and file name each receipt gets inside the zips,
which zip it goes into, and the CSV that lists them. Pure functions: no database, no storage.

- *Names.* A vendor name, a file name or a year becomes one path component that every platform
  accepts: path separators, control and format characters (zero-width, bidi overrides) and the
  characters Windows refuses are replaced or dropped, leading dots and trailing dots and spaces
  removed, Windows' reserved names (`CON`, `NUL`, `COM1`...) avoided, and the length cut. Two
  vendors whose names end up the same folder (also differing only by case: macOS and Windows)
  share it, and two files with the same name in a folder become `name (1).pdf`, compared
  ignoring case, across every zip, so extracting all the zips into one folder merges cleanly.
- *Parts.* Items are sorted by path and packed in order; a zip is closed when the next file would
  take it over the part size (a file is never cut in two; a file bigger than the part size goes
  alone).
- *CSV.* `date, vendor, type, amount, currency, file, part`, every cell through the formula guard
  (`filters._cell`).
"""

import csv
import io
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from .filters import _cell

#: Longest folder name (a vendor) and file name, in characters. Windows' whole-path limit is 260;
#: a path here is a year or vendor folder, another folder and the file name.
FOLDER_NAME_MAX = 80
FILE_NAME_MAX = 120

NO_DATE = 'No date'
NO_VENDOR = 'No vendor'

CSV_NAME = 'receipts.csv'
CSV_HEADER = ('date', 'vendor', 'type', 'amount', 'currency', 'file', 'part')

_REFUSED = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {
    'CON',
    'PRN',
    'AUX',
    'NUL',
    *(f'COM{n}' for n in range(1, 10)),
    *(f'LPT{n}' for n in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class ExportRow:
    """One receipt as the planner sees it (its data is copied into the export)."""

    receipt_id: UUID
    file_key: str
    size_bytes: int
    file_name: str
    receipt_date: date | None
    vendor: str
    document_type: str
    amount: Decimal | None
    currency: str
    created_at: datetime


@dataclass(slots=True)
class PlannedItem:
    row: ExportRow
    path: str
    part_number: int = 0


@dataclass(slots=True)
class Plan:
    items: list[PlannedItem] = field(default_factory=list)
    part_count: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(item.row.size_bytes for item in self.items)


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


def _split_extension(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition('.')
    if not dot or not stem or len(ext) > 10 or ' ' in ext:
        return name, ''
    return stem, f'.{ext}'


class _Folders:
    """Folder names, case-insensitively unique: the first spelling of a folder wins."""

    def __init__(self) -> None:
        self._spelling: dict[str, str] = {}

    def resolve(self, parent: str, name: str) -> str:
        key = f'{parent}/{name}'.casefold()
        if key not in self._spelling:
            self._spelling[key] = name
        return self._spelling[key]


def _year(row: ExportRow) -> str:
    return str(row.receipt_date.year) if row.receipt_date else NO_DATE


def _row_order(row: ExportRow) -> tuple:
    return (
        row.receipt_date is None,
        row.receipt_date or date.min,
        row.created_at,
        str(row.receipt_id),
    )


def plan_export(rows: list[ExportRow], layout: str, part_bytes: int) -> Plan:
    """Give every row its path and its part. `layout` is `year_vendor` or `vendor_year`."""
    if layout not in ('year_vendor', 'vendor_year'):
        raise ValueError(f'Unknown layout: {layout}')
    folders = _Folders()
    used: set[str] = set()
    items: list[PlannedItem] = []
    for row in sorted(rows, key=_row_order):
        year = _year(row)
        vendor = clean_folder_name(row.vendor, NO_VENDOR)
        first, second = (year, vendor) if layout == 'year_vendor' else (vendor, year)
        first = folders.resolve('', first)
        second = folders.resolve(first, second)
        directory = f'{first}/{second}'
        name = clean_file_name(row.file_name)
        stem, ext = _split_extension(name)
        path, n = f'{directory}/{name}', 0
        while path.casefold() in used:
            n += 1
            path = f'{directory}/{stem} ({n}){ext}'
        used.add(path.casefold())
        items.append(PlannedItem(row=row, path=path))

    items.sort(key=lambda item: (item.path.casefold(), item.path))
    part, filled = 1, 0
    for item in items:
        if filled and filled + item.row.size_bytes > part_bytes:
            part, filled = part + 1, 0
        item.part_number = part
        filled += item.row.size_bytes
    return Plan(items=items, part_count=part if items else 0)


def part_file_name(created: date, number: int, count: int) -> str:
    """What a part is downloaded as: `receipts-2026-11-16-part-01-of-05.zip` (no suffix for an
    export that fits one zip)."""
    base = f'receipts-{created.isoformat()}'
    return f'{base}.zip' if count <= 1 else f'{base}-part-{number:02d}-of-{count:02d}.zip'


@dataclass(frozen=True, slots=True)
class CsvEntry:
    """One CSV line: what `ReceiptExportItem` holds."""

    receipt_date: date | None
    vendor: str
    document_type: str
    amount: Decimal | None
    currency: str
    path: str
    part_number: int


def iter_export_csv(entries: Iterator[CsvEntry] | list[CsvEntry]) -> Iterator[bytes]:
    """The CSV as UTF-8 lines (header first, `\\r\\n` endings like the dashboard's export), for
    writing into a zip entry."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')

    def flush() -> bytes:
        line = buffer.getvalue().encode()
        buffer.seek(0)
        buffer.truncate()
        return line

    writer.writerow(CSV_HEADER)
    yield flush()
    for entry in entries:
        writer.writerow(
            _cell(value)
            for value in (
                entry.receipt_date.isoformat() if entry.receipt_date else '',
                entry.vendor,
                entry.document_type,
                '' if entry.amount is None else f'{entry.amount:.2f}',
                entry.currency,
                entry.path,
                entry.part_number,
            )
        )
        yield flush()
