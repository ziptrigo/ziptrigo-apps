"""Receipt reports (issue #351): how much was spent, by vendor or by category, in a period.

One aggregate query (`build_report`) feeds the Reports page, the three downloads (`report_files`)
and the JWT API, so they can't disagree. It counts the user's *live* receipts only (never the
Trash, never an unfinished upload) and, by default, only the *processed* ones: a receipt that
still needs review has missing required fields, and one still being read or failed has no
reliable amount. "Processed only" off adds the needs-review receipts and nothing else.

**Amounts in different currencies are never added together.** Rows are grouped by (vendor or
category, currency), the totals are one per currency, and a row's share is a share of *its
currency's* total. A receipt with no currency is its own group (`currency == ''`).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Count, Max, QuerySet, Sum

from apps.accounts.models import User
from apps.core.datetime_format import PUBLIC, DisplayFormats, format_date

from ..models import Receipt, ReceiptStatus

GROUP_VENDOR = 'vendor'
GROUP_CATEGORY = 'category'
GROUPS = (GROUP_VENDOR, GROUP_CATEGORY)
DEFAULT_GROUP = GROUP_VENDOR

#: The statuses a report counts: processed receipts, plus the ones needing review when
#: "processed only" is off. Failed and in-progress receipts are never counted.
PROCESSED_ONLY_STATUSES = (ReceiptStatus.PROCESSED,)
INCLUDING_REVIEW_STATUSES = (ReceiptStatus.PROCESSED, ReceiptStatus.NEEDS_REVIEW)

NO_VENDOR_NAME = '(no vendor)'
NO_CATEGORY_NAME = 'Uncategorized'
#: How a receipt with no currency is labelled.
NO_CURRENCY_LABEL = 'no currency'

ZERO = Decimal('0.00')
_CENTS = Decimal('0.01')


@dataclass(frozen=True, slots=True)
class ReportFilters:
    """What a report covers. The dates bound `receipt_date` (inclusive; a receipt with no date is
    in a report only when no bound is set)."""

    group: str = DEFAULT_GROUP
    date_from: date | None = None
    date_to: date | None = None
    processed_only: bool = True
    vetted_only: bool = False


@dataclass(slots=True)
class ReportRow:
    """One vendor (or category) in one currency. `key` is the vendor or category id, `None` for
    receipts with none; `vetted` is a vendor's state (always `None` in a category report)."""

    key: int | None
    name: str
    currency: str
    count: int
    total: Decimal
    average: Decimal
    last_date: date | None
    vetted: bool | None = None
    share: Decimal = ZERO  # percent of this currency's total, one decimal
    priced: int = 0  # receipts with an amount: what the average is over

    @property
    def currency_label(self) -> str:
        return self.currency or NO_CURRENCY_LABEL

    @property
    def total_text(self) -> str:
        return money_text(self.total)

    @property
    def average_text(self) -> str:
        return money_text(self.average)


@dataclass(slots=True)
class CurrencyTotal:
    currency: str
    count: int
    total: Decimal
    average: Decimal

    @property
    def currency_label(self) -> str:
        return self.currency or NO_CURRENCY_LABEL

    @property
    def total_text(self) -> str:
        return money_text(self.total)

    @property
    def average_text(self) -> str:
        return money_text(self.average)


@dataclass(slots=True)
class Report:
    filters: ReportFilters
    rows: list[ReportRow] = field(default_factory=list)
    totals: list[CurrencyTotal] = field(default_factory=list)
    receipt_count: int = 0
    group_count: int = 0
    """How many different vendors (or categories) the rows are of."""

    @property
    def is_empty(self) -> bool:
        return not self.rows


def _money(value: Decimal | None) -> Decimal:
    return (value or ZERO).quantize(_CENTS)


def report_receipts(user: User, filters: ReportFilters) -> QuerySet:
    """The receipts a report is made of."""
    statuses = PROCESSED_ONLY_STATUSES if filters.processed_only else INCLUDING_REVIEW_STATUSES
    queryset = Receipt.objects.filter(
        user=user, uploaded=True, deleted_at__isnull=True, status__in=statuses
    )
    if filters.vetted_only:
        queryset = queryset.filter(vendor_ref__vetted_at__isnull=False)
    if filters.date_from:
        queryset = queryset.filter(receipt_date__gte=filters.date_from)
    if filters.date_to:
        queryset = queryset.filter(receipt_date__lte=filters.date_to)
    return queryset


def _sort_key(row: ReportRow) -> tuple:
    # Currencies in alphabetical order with "no currency" last; largest total first within one.
    return (row.currency == '', row.currency, -row.total, row.name.casefold(), row.key or 0)


def build_report(user: User, filters: ReportFilters) -> Report:
    """The report: one aggregate query, no query per row."""
    if filters.group not in GROUPS:
        raise ValueError(f'Unknown report group: {filters.group}')
    queryset = report_receipts(user, filters)
    by_vendor = filters.group == GROUP_VENDOR
    if by_vendor:
        fields = ('vendor_ref_id', 'vendor_ref__name', 'vendor_ref__vetted_at', 'currency')
    else:
        fields = ('category_id', 'category__name', 'currency')
    aggregated = (
        queryset.order_by()
        .values(*fields)
        .annotate(
            receipts=Count('pk'),
            priced=Count('amount'),
            total=Sum('amount'),
            last_date=Max('receipt_date'),
        )
    )

    rows: list[ReportRow] = []
    for item in aggregated:
        if by_vendor:
            key, name, vetted_at, currency = (
                item['vendor_ref_id'],
                item['vendor_ref__name'],
                item['vendor_ref__vetted_at'],
                item['currency'],
            )
            name = name if key is not None else NO_VENDOR_NAME
            vetted = vetted_at is not None if key is not None else None
        else:
            key, name, currency = item['category_id'], item['category__name'], item['currency']
            name = name if key is not None else NO_CATEGORY_NAME
            vetted = None
        total = _money(item['total'])
        priced = item['priced']
        rows.append(
            ReportRow(
                key=key,
                name=name,
                currency=currency,
                count=item['receipts'],
                total=total,
                average=_money(total / priced) if priced else ZERO,
                last_date=item['last_date'],
                vetted=vetted,
                priced=priced,
            )
        )

    # One total per currency, from the rows (the average is over the receipts that have an amount).
    sums: dict[str, list] = {}
    for row in rows:
        bucket = sums.setdefault(row.currency, [0, ZERO, 0])
        bucket[0] += row.count
        bucket[1] += row.total
        bucket[2] += row.priced
    totals = [
        CurrencyTotal(
            currency=currency,
            count=count,
            total=total,
            average=_money(total / priced) if priced else ZERO,
        )
        for currency, (count, total, priced) in sorted(
            sums.items(), key=lambda item: (item[0] == '', item[0])
        )
    ]

    total_of = {t.currency: t.total for t in totals}
    for row in rows:
        whole = total_of[row.currency]
        row.share = (row.total * 100 / whole).quantize(Decimal('0.1')) if whole else Decimal('0.0')
    rows.sort(key=_sort_key)
    return Report(
        filters=filters,
        rows=rows,
        totals=totals,
        receipt_count=sum(t.count for t in totals),
        group_count=len({(row.key, row.name) for row in rows}),
    )


def describe_filters(filters: ReportFilters, formats: DisplayFormats | None = None) -> str:
    """What the report covers, as one sentence ("Receipts dated 01/01/2026 to 12/31/2026,
    processed only."). `formats` are the date wording (the active ones by default; the downloads
    pass `PUBLIC`)."""
    if filters.date_from and filters.date_to:
        when = (
            f'Receipts dated {format_date(filters.date_from, "full", formats)} '
            f'to {format_date(filters.date_to, "full", formats)}'
        )
    elif filters.date_from:
        when = f'Receipts dated from {format_date(filters.date_from, "full", formats)}'
    elif filters.date_to:
        when = f'Receipts dated until {format_date(filters.date_to, "full", formats)}'
    else:
        when = 'Receipts of all dates'
    parts = [when, 'processed only' if filters.processed_only else 'processed and needs review']
    if filters.vetted_only:
        parts.append('vetted vendors only')
    return ', '.join(parts) + '. Receipts in the Trash are never counted.'


def report_title(group: str) -> str:
    return f'Receipts by {group}'


def public_description(filters: ReportFilters) -> str:
    return describe_filters(filters, PUBLIC)


def money_text(value: Decimal) -> str:
    """`1,234.56`: two decimals, thousands separated (the amount only; the currency is its own
    column)."""
    return f'{value:,.2f}'


__all__ = (
    'DEFAULT_GROUP',
    'GROUPS',
    'GROUP_CATEGORY',
    'GROUP_VENDOR',
    'NO_CATEGORY_NAME',
    'NO_CURRENCY_LABEL',
    'NO_VENDOR_NAME',
    'CurrencyTotal',
    'Report',
    'ReportFilters',
    'ReportRow',
    'build_report',
    'describe_filters',
    'money_text',
    'public_description',
    'report_receipts',
    'report_title',
)
