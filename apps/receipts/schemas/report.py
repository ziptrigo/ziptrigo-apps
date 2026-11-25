"""Schemas for the report endpoint (`GET /api/receipts/reports`, issue #351)."""

from datetime import date
from decimal import Decimal
from typing import Literal

from ninja import Schema


class ReceiptReportRowSchema(Schema):
    """One vendor (or category) in one currency. A vendor in two currencies has two rows.

    `id` is the vendor's or category's id, `null` for the receipts that have none (`name` is then
    "(no vendor)" or "Uncategorized"). `vetted` is a vendor's state (`null` in a category report
    and for receipts with no vendor). `total` and `average` are amounts with two decimals (the
    average is over the receipts that have an amount); `share` is the percent of this row's
    *currency* total, with one decimal."""

    id: int | None
    name: str
    vetted: bool | None
    currency: str
    receipt_count: int
    total: Decimal
    share: Decimal
    average: Decimal
    last_receipt_date: date | None


class ReceiptReportTotalSchema(Schema):
    """The total of one currency. Amounts in different currencies are never added together."""

    currency: str
    receipt_count: int
    total: Decimal
    average: Decimal


class ReceiptReportSchema(Schema):
    """Receipts by vendor or by category in a period. `date_from` / `date_to` are the dates the
    request resolved to (`null`: no bound); `description` says in words what is counted. Rows are
    sorted by currency, then by total, largest first; `currency` is `""` for receipts with none."""

    group: Literal['vendor', 'category']
    date_from: date | None
    date_to: date | None
    processed_only: bool
    vetted_only: bool
    description: str
    receipt_count: int
    rows: list[ReceiptReportRowSchema]
    totals: list[ReceiptReportTotalSchema]
