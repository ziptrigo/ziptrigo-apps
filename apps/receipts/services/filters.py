"""Listing and exporting a user's receipts: one filter definition shared by the dashboard, the API
and the CSV export, so all three always agree on what "the current filters" means."""

import csv
import io
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import BigIntegerField, CharField, Count, F, Q, QuerySet, Value
from django.db.models.functions import Abs, Cast, Concat, LPad, Mod, Round

from apps.accounts.models import User

from ..models import IN_PROGRESS_STATUSES, Receipt, ReceiptStatus
from .ai import THOUSANDS_COMMAS

DEFAULT_SORT = '-receipt_date'
SORTS = ('receipt_date', '-receipt_date', 'created_at', '-created_at')

#: `category=` value that selects receipts with no category.
UNCATEGORIZED = 'none'

#: A search term that can also be (part of) an amount: digits with at most one `.` or `,` as the
#: decimal separator (`75`, `275.5`, `.50`, `275,50`), optionally after a currency symbol.
_AMOUNT_TERM = re.compile(r'[$€£]?\s*(\d*[.,]?\d*)')

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
    'vendor_as_read',
)


@dataclass(slots=True)
class ReceiptFilters:
    """What the dashboard/API/export can filter by. `status` is one status, several (a list, or a
    comma-separated string), or `None` for all; `category` is a category id, `'none'` for
    uncategorized, or `None`; `vendor` is a vendor id; `q` searches the vendor (also by the name the
    AI read, so a receipt is still found by it after its vendor was renamed) and the notes, and the
    amount (as text with two decimals, so `75` finds 275.50) when it looks like a number; the dates
    bound `receipt_date` (inclusive); `duplicates` keeps only receipts flagged as possible
    duplicates."""

    status: str | Iterable[str] | None = None
    category: int | str | None = None
    vendor: int | None = None
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
    # `uploaded=False` is a direct upload still in flight (or abandoned): no file, not a receipt
    # yet, and never to be listed, counted or exported.
    queryset = Receipt.objects.filter(user=user, uploaded=True, deleted_at__isnull=not trashed)

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

    if filters.vendor is not None:
        try:
            queryset = queryset.filter(vendor_ref_id=int(filters.vendor))
        except TypeError, ValueError:
            raise ValidationError('Unknown vendor.') from None

    term = filters.q.strip()
    if term:
        matches = (
            Q(vendor__icontains=term)
            | Q(vendor_extracted__icontains=term)
            | Q(notes__icontains=term)
        )
        amount_term = _amount_term(term)
        if amount_term:
            queryset = queryset.annotate(amount_text=_amount_text())
            matches |= Q(amount_text__contains=amount_term)
        queryset = queryset.filter(matches)
    if filters.date_from:
        queryset = queryset.filter(receipt_date__gte=filters.date_from)
    if filters.date_to:
        queryset = queryset.filter(receipt_date__lte=filters.date_to)
    if filters.duplicates:
        queryset = queryset.filter(possible_duplicate_of__isnull=False, duplicate_dismissed=False)

    return queryset.order_by(*_ordering(filters.sort, trashed=trashed))


def _amount_term(term: str) -> str:
    """`term` as it would appear in an amount's text (`'275,5'` -> `'275.5'`), or `''` when it
    isn't a number."""
    match = _AMOUNT_TERM.fullmatch(term)
    if not match or not any(char.isdigit() for char in match[1]):
        return ''
    text = match[1]
    if THOUSANDS_COMMAS.fullmatch(text):  # as a typed amount reads it: `1,234` is 1234
        return text.replace(',', '')
    return text.replace(',', '.')


def _amount_text() -> Concat:
    """`amount` as text with exactly two decimals and no sign (`275.50`), built the same way on
    Postgres and SQLite: casting the decimal itself gives `275.50` on one and `275.5` on the other.
    Rounded to whole cents first, so a float on SQLite (`0.29 * 100 = 28.999...`) can't lose one,
    and the remainder cast back to an integer, since SQLite's `MOD` answers a float (`5.0`)."""
    cents = Cast(Round(Abs(F('amount')) * 100), BigIntegerField())
    remainder = Cast(Mod(cents, 100), BigIntegerField())
    return Concat(
        Cast(cents / 100, CharField()),
        Value('.'),
        LPad(Cast(remainder, CharField()), 2, Value('0')),
        output_field=CharField(),
    )


def dashboard_counts(user: User) -> dict[str, int]:
    """The numbers on the dashboard's tabs and Trash link, in one query: how many live receipts
    `needs_review`, are `in_progress` (received, processing or waiting for credits), `failed` or
    flagged as `duplicates`, plus the `total` and what is in the `trash`."""
    live = Q(deleted_at__isnull=True)
    return Receipt.objects.filter(user=user, uploaded=True).aggregate(
        total=Count('pk', filter=live),
        needs_review=Count('pk', filter=live & Q(status=ReceiptStatus.NEEDS_REVIEW)),
        in_progress=Count('pk', filter=live & Q(status__in=IN_PROGRESS_STATUSES)),
        failed=Count('pk', filter=live & Q(status=ReceiptStatus.FAILED)),
        duplicates=Count(
            'pk', filter=live & Q(possible_duplicate_of__isnull=False, duplicate_dismissed=False)
        ),
        trash=Count('pk', filter=Q(deleted_at__isnull=False)),
    )


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

_FORMULA_PREFIXES = ('=', '+', '-', '@')
_CONTROL_PREFIXES = ('\t', '\r')


def _cell(value: object) -> str:
    """One CSV cell. A text that a spreadsheet would read as a formula (it starts with `=`, `+`,
    `-`, `@`...), even after leading whitespace (some importers trim it), is prefixed with an
    apostrophe: vendors and notes come from emails and the AI, and `=HYPERLINK(...)` in an
    exported file is a real attack. Every cell goes through here."""
    if value is None:
        return ''
    text = str(value)
    if text.startswith(_CONTROL_PREFIXES) or text.lstrip().startswith(_FORMULA_PREFIXES):
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
            _cell(value)
            for value in (
                receipt.receipt_date.isoformat() if receipt.receipt_date else '',
                receipt.vendor,
                '' if receipt.amount is None else f'{receipt.amount:.2f}',
                receipt.currency,
                receipt.category.name if receipt.category else '',
                receipt.document_type,
                receipt.document_number,
                receipt.payment_method,
                receipt.notes,
                receipt.status,
                receipt.source,
                receipt.created_at.isoformat(),
                receipt.original_filename,
                receipt.vendor_extracted,
            )
        )
        yield flush()


def export_csv(user: User, filters: ReceiptFilters | None = None) -> str:
    """The whole export as one string."""
    return ''.join(iter_csv(user, filters))
