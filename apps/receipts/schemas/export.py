"""Schemas for the file export endpoints (`/api/receipts/exports`, issue #315): the quote, the
export and its zip files, and the download link of one zip file.

Every class name starts with `ReceiptExport`: the OpenAPI document keys a component by class name
alone, so a name another app also uses would silently replace it.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field


class ReceiptExportRequestSchema(Schema):
    """Body for `POST /exports/quote`: which receipts' files to pack (the filters of
    `GET /receipts`, same meaning; the Trash and receipts with no stored file are never included)
    and how to organize them in the zip files.

    - `layout`: `year_vendor` (default: `2026/Acme/invoice.pdf`) or `vendor_year`
      (`Acme/2026/invoice.pdf`). Anything else is a 400.
    - `status`: one or more of `received`, `processing`, `waiting_for_credits`, `needs_review`,
      `processed`, `failed`, comma-separated; `in_progress` stands for the first three.
    - `category`: a category id, or `none` for uncategorized. `vendor`: a vendor id.
    - `q`: matches the vendor, the notes and, for a number like `75`, the amount.
    - `date_from` / `date_to`: `YYYY-MM-DD`, inclusive, applied to `receipt_date` (a malformed
      date is a 400).
    - `duplicates`: only receipts flagged as possible duplicates.
    """

    model_config = ConfigDict(extra='forbid')

    layout: str = 'year_vendor'
    status: str | None = None
    category: str | None = None
    vendor: int | None = None
    q: str = ''
    date_from: str | None = None
    date_to: str | None = None
    duplicates: bool = False


class ReceiptExportCreateSchema(ReceiptExportRequestSchema):
    """Body for `POST /exports`: the quote's fields and `expected_cost`.

    `expected_cost` is the total in credits the client confirmed (the quote's `cost`). If what
    the export costs now is different (receipts were added or removed, or the price changed) the
    answer is a 409 naming the real total and nothing is charged or created. Left out, the export
    is created whatever it costs, for a client with no one to ask.
    """

    expected_cost: int | None = Field(default=None, ge=0)


class ReceiptExportQuoteSchema(Schema):
    """What an export of these filters would be. `cost` is `file_count x price_per_file` credits
    (`0` is free). `affordable` is `balance >= cost`; `frozen` says the account's storage is
    paused (an export would be refused with a 402). `retention_days` is how long the zip files
    stay downloadable after they are ready; `part_bytes` is about how big each zip file is."""

    file_count: int
    total_bytes: int
    part_count: int
    price_per_file: int
    cost: int
    balance: int
    affordable: bool
    frozen: bool
    retention_days: int
    part_bytes: int


class ReceiptExportPartSchema(Schema):
    """One zip file of an export. The zip files are independent (each opens on its own);
    `receipts.csv` is in number 1. `built` is false while it is still being written (`size_bytes`
    is 0 until then); `filename` is the name it downloads as."""

    number: int
    filename: str
    size_bytes: int
    file_count: int
    built: bool


class ReceiptExportFilterInfoSchema(Schema):
    """What the export was made from: `summary` is short lines for display (`Vendor: Acme`; empty
    for everything) and `params` the filters that were used (the body's keys)."""

    summary: list[str]
    params: dict[str, Any]


class ReceiptExportSchema(Schema):
    """One export, as every export endpoint returns it.

    `status` is `building` (the zip files are being written: poll until it changes), `ready`
    (download the parts until `expires_at`), `failed` (it could not be built and every credit was
    refunded) or `expired` (past its retention; the files are gone). A deleted export is a 404.

    `charged_credits` was spent when the export was created; `refunded_credits` is given back for
    a failed export (all of it) or for files that had vanished by the time they were zipped
    (`skipped_count`, left out of the zip files). `file_count` is the number planned, so the zip
    files hold `file_count - skipped_count` once ready. `parts` is empty for a failed or expired
    export (its rows are gone). `ready_at` and `expires_at` are `null` until it is ready.
    """

    id: UUID
    status: Literal['building', 'ready', 'failed', 'expired']
    layout: Literal['year_vendor', 'vendor_year']
    filters: ReceiptExportFilterInfoSchema
    file_count: int
    skipped_count: int
    total_bytes: int
    part_count: int
    price_per_file: int
    charged_credits: int
    refunded_credits: int
    parts: list[ReceiptExportPartSchema]
    created_at: datetime
    ready_at: datetime | None
    expires_at: datetime | None


class ReceiptExportListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[ReceiptExportSchema]


class ReceiptExportPartUrlSchema(Schema):
    """A short-lived presigned URL for one zip file: `GET` it (no `Authorization` header) within
    `expires_in` seconds; it downloads as `filename`. Never cache it; ask again."""

    url: str
    filename: str
    size_bytes: int
    expires_in: int
