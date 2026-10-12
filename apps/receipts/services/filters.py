"""Listing and exporting a user's receipts: one filter definition shared by the dashboard, the API
and the CSV export, so all three always agree on what "the current filters" means."""

import csv
import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import F, Q, QuerySet

from apps.accounts.models import User

from ..models import Receipt, ReceiptStatus

DEFAULT_SORT = '-receipt_date'
SORTS = ('receipt_date', '-receipt_date', 'created_at', '-created_at')

#: `category=` value that selects receipts with no category.
UNCATEGORIZED = 'none'

CSV_COLUMNS = (
    'receipt_date',
    'vendor',
    'amount',
    'currency',
    'category',
    'document_type',
    'document_number',
    'payment_method',
    'notes',
    'status',
    'source',
    'added',
    'original_filename',
)


@dataclass(slots=True)
class ReceiptFilters:
    """What the dashboard/API/export can filter by. `status` is one status, several (a list, or a
    comma-separated string), or `None` for all; `category` is a category id, `'none'` for
    uncategorized, or `None`; `q` searches vendor and notes; the dates bound `receipt_date`
    (inclusive); `duplicates` keeps only receipts flagged as possible duplicates."""

    status: str | Iterable[str] | None = None
    category: int | str | None = None
    q: str = ''
    date_from: date | None = None
    date_to: date | None = None
    duplicates: bool = False
    sort: str = DEFAULT_SORT


def _statuses(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    parts = value.split(',') if isinstance(value, str) else list(value)
    statuses = [part.strip() for part in parts if part and part.strip()]
    valid = set(ReceiptStatus.values)
    bad = [status for status in statuses if status not in valid]
    if bad:
        raise ValidationError(f'Unknown status: {", ".join(bad)}.')
    return statuses


def filter_receipts(
    user: User, filters: ReceiptFilters | None = None, *, trashed: bool = False
) -> QuerySet:
    """The user's receipts matching `filters`, sorted. `trashed=True` lists the Trash instead of
    the live receipts. Raises `ValidationError` for an unknown status, sort or category."""
    filters = filters or ReceiptFilters()
    queryset = Receipt.objects.filter(user=user, deleted_at__isnull=not trashed)

    statuses = _statuses(filters.status)
    if statuses:
        queryset = queryset.filter(status__in=statuses)

    if filters.category not in (None, ''):
        if filters.category == UNCATEGORIZED:
            queryset = queryset.filter(category__isnull=True)
        else:
            try:
                queryset = queryset.filter(category_id=int(filters.category))
            except TypeError, ValueError:
                raise ValidationError('Unknown category.') from None

    term = filters.q.strip()
    if term:
        queryset = queryset.filter(Q(vendor__icontains=term) | Q(notes__icontains=term))
    if filters.date_from:
        queryset = queryset.filter(receipt_date__gte=filters.date_from)
    if filters.date_to:
        queryset = queryset.filter(receipt_date__lte=filters.date_to)
    if filters.duplicates:
        queryset = queryset.filter(possible_duplicate_of__isnull=False, duplicate_dismissed=False)

    return queryset.order_by(*_ordering(filters.sort, trashed=trashed))


def _ordering(sort: str, *, trashed: bool) -> list:
    if sort not in SORTS:
        raise ValidationError(f'Unknown sort: {sort}.')
    if trashed:
        return ['-deleted_at', '-created_at']
    match sort:
        case 'receipt_date':  # oldest first; receipts without a date last
            return [F('receipt_date').asc(nulls_last=True), '-created_at']
        case '-receipt_date':  # newest first; receipts without a date on top, where they get seen
            return [F('receipt_date').desc(nulls_first=True), '-created_at']
        case 'created_at':
            return ['created_at', 'id']
        case _:
            return ['-created_at', '-id']


# -- CSV export ---------------------------------------------------------------------------------

_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _cell(value: object) -> str:
    """One CSV cell. A text that a spreadsheet would read as a formula (it starts with `=`, `+`,
    `-`, `@`...) is prefixed with an apostrophe: vendors and notes come from emails and the AI,
    and `=HYPERLINK(...)` in an exported file is a real attack."""
    if value is None:
        return ''
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def iter_csv(user: User, filters: ReceiptFilters | None = None) -> Iterator[str]:
    """The export as CSV text, one line at a time (header first), for a streaming response. Only
    live receipts, with the same filters as the dashboard."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')

    def flush() -> str:
        line = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return line

    writer.writerow(CSV_COLUMNS)
    yield flush()
    queryset = filter_receipts(user, filters).select_related('category')
    for receipt in queryset.iterator(chunk_size=500):
        writer.writerow(
            [
                receipt.receipt_date.isoformat() if receipt.receipt_date else '',
                _cell(receipt.vendor),
                '' if receipt.amount is None else f'{receipt.amount:.2f}',
                receipt.currency,
                _cell(receipt.category.name if receipt.category else ''),
                receipt.document_type,
                _cell(receipt.document_number),
                _cell(receipt.payment_method),
                _cell(receipt.notes),
                receipt.status,
                receipt.source,
                receipt.created_at.isoformat(),
                _cell(receipt.original_filename),
            ]
        )
        yield flush()


def export_csv(user: User, filters: ReceiptFilters | None = None) -> str:
    """The whole export as one string."""
    return ''.join(iter_csv(user, filters))
