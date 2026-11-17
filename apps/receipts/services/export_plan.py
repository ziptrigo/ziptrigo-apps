"""Planning an export (issue #315): which folder and file name each receipt gets inside the zips,
which zip it goes into, and the CSV that lists them. Pure functions: no database, no storage.

The naming rules (a vendor name, a file name or a year becomes one path component every platform
accepts; case-insensitive uniqueness across every zip) and the packing into zips are shared with
the To-do export and live in `apps.core.services.export_names`; what is Receipts' own here is the
layout (`Year/Vendor` or `Vendor/Year`), the order of the rows and the CSV.

- *Parts.* Items are sorted by path and packed in order; a zip is closed when the next file would
  take it over the part size (a file is never cut in two; a file bigger than the part size goes
  alone).
- *CSV.* `date, vendor, type, amount, currency, file, part`, every cell through the formula guard
  (`cell_text`).
"""

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from apps.core.services.export_names import (
    FILE_NAME_MAX,
    FOLDER_NAME_MAX,
    Folders,
    cell_text,
    clean_file_name,
    clean_folder_name,
    pack_parts,
    unique_path,
)
from apps.core.services.export_names import part_file_name as _part_file_name

__all__ = [
    'CSV_HEADER',
    'CSV_NAME',
    'FILE_NAME_MAX',
    'FOLDER_NAME_MAX',
    'NO_DATE',
    'NO_VENDOR',
    'CsvEntry',
    'ExportRow',
    'Plan',
    'PlannedItem',
    'clean_file_name',
    'clean_folder_name',
    'iter_export_csv',
    'part_file_name',
    'plan_export',
]

NO_DATE = 'No date'
NO_VENDOR = 'No vendor'

CSV_NAME = 'receipts.csv'
CSV_HEADER = ('date', 'vendor', 'type', 'amount', 'currency', 'file', 'part')


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
    folders = Folders()
    used: set[str] = set()
    items: list[PlannedItem] = []
    for row in sorted(rows, key=_row_order):
        year = _year(row)
        vendor = clean_folder_name(row.vendor, NO_VENDOR)
        first, second = (year, vendor) if layout == 'year_vendor' else (vendor, year)
        first = folders.resolve('', first)
        second = folders.resolve(first, second)
        path = unique_path(f'{first}/{second}', clean_file_name(row.file_name), used)
        items.append(PlannedItem(row=row, path=path))

    items.sort(key=lambda item: (item.path.casefold(), item.path))
    numbers = pack_parts((item.row.size_bytes for item in items), part_bytes)
    for item, number in zip(items, numbers, strict=True):
        item.part_number = number
    return Plan(items=items, part_count=numbers[-1] if numbers else 0)


def part_file_name(created: date, number: int, count: int) -> str:
    """What a part is downloaded as: `receipts-2026-11-16-part-01-of-05.zip` (no suffix for an
    export that fits one zip)."""
    return _part_file_name('receipts', created, number, count)


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
            cell_text(value)
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
