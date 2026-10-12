"""Schemas for the receipts JWT API (`/api/receipts/`): one receipt, a page of them, the partial
update, and the short-lived file URL."""

from datetime import date, datetime
from uuid import UUID

from ninja import Schema


class CategoryRefSchema(Schema):
    """A receipt's category: enough to show and to filter by, without the full category row."""

    id: int
    name: str


class ReceiptSchema(Schema):
    """One receipt, as every read and write endpoint returns it.

    Never exposes the S3 keys or the raw AI response. `amount` is a string with two decimals
    (`"42.10"`), never a float, so no client rounds it; it is `null` until one is set.
    `possible_duplicate_of` is the id of the receipt this one may repeat (cleared once the user
    says it is not a duplicate: `duplicate_dismissed`).
    """

    id: UUID
    status: str
    failure_reason: str
    source: str
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    vendor: str
    amount: str | None
    currency: str
    receipt_date: date | None
    document_type: str
    document_number: str
    payment_method: str
    category: CategoryRefSchema | None
    notes: str
    possible_duplicate_of: UUID | None
    duplicate_dismissed: bool
    missing_fields: list[str]
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None
    deleted_at: datetime | None

    @staticmethod
    def resolve_amount(obj) -> str | None:
        return None if obj.amount is None else f'{obj.amount:.2f}'

    @staticmethod
    def resolve_category(obj) -> dict | None:
        category = obj.category
        return None if category is None else {'id': category.pk, 'name': category.name}

    @staticmethod
    def resolve_possible_duplicate_of(obj) -> UUID | None:
        return obj.possible_duplicate_of_id

    @staticmethod
    def resolve_missing_fields(obj) -> list[str]:
        """Which of vendor, amount and receipt date are still unset (empty once settled)."""
        return obj.missing_required_fields


class ReceiptListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[ReceiptSchema]


class ReceiptUpdateSchema(Schema):
    """Body for `PATCH /receipts/{id}`. Every field is optional and only the ones present in the
    body are applied (`payload.dict(exclude_unset=True)`), so omitting a field leaves it as it is;
    an explicit `null` (or an empty string) clears it. `amount` takes `"42.10"` or `42.1`;
    `receipt_date` is `YYYY-MM-DD`; `category` is a category id."""

    vendor: str | None = None
    amount: str | float | None = None
    currency: str | None = None
    receipt_date: str | None = None
    document_type: str | None = None
    document_number: str | None = None
    payment_method: str | None = None
    category: int | None = None
    notes: str | None = None


class FileUrlSchema(Schema):
    """A short-lived presigned URL for the original file (or its inline preview)."""

    url: str
    disposition: str
    filename: str
    expires_in: int
