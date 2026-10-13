"""Schemas for the receipts JWT API (`/api/receipts/`): one receipt, a page of them, the partial
update, and the short-lived file URL."""

from datetime import date, datetime
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict


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

    `vendor` is the vendor's name, as it has always been. `vendor_id` is the user's vendor it is
    filed under (`GET /vendors/{id}`; `null` while no vendor was read) and `vendor_vetted` whether
    the user has confirmed that vendor. `vendor_extracted` is the name the AI read on the receipt,
    before matching: it differs from `vendor` once the vendor was renamed, or the receipt moved to
    another vendor, and is empty for a receipt not read yet.
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
    vendor_id: int | None
    vendor_vetted: bool
    vendor_extracted: str
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
    def resolve_vendor_id(obj) -> int | None:
        return obj.vendor_ref_id

    @staticmethod
    def resolve_vendor_vetted(obj) -> bool:
        return obj.vendor_ref is not None and obj.vendor_ref.vetted

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
    body are applied (`payload.dict(exclude_unset=True)`), so omitting a field leaves it as it is.

    An explicit `null` **clears** the field (`{"vendor": null}`; for a text field an empty string
    does the same). This differs from `PATCH /inbox`, where `null` means "unchanged". `amount`
    takes `"42.10"` or `42.1`; `receipt_date` is `YYYY-MM-DD`; `category` is a category id (or
    `null` for uncategorized -- not an empty string, which is a 422 for an integer field).

    The vendor is either `vendor` (a name: the receipt moves to the vendor of that name or key, or
    to a new one) or `vendor_id` (one of your vendors; `null` clears it, like `vendor: null`),
    never both (a 400). Either moves **this receipt** only, vets the vendor and teaches the matcher
    the name the AI read on it; `PATCH /vendors/{id}` renames a vendor for all its receipts.
    Sending the vendor the receipt already has (the same `vendor_id`, or the same name) changes
    nothing, its vetted state included: to vet a vendor, send `PATCH /vendors/{id}` with
    `{"vetted": true}`.

    A key that is not one of these fields (a typo, or `status`, `id`, ...) is refused with a 422
    rather than ignored, so a client can't mistake a no-op for a saved edit.
    """

    model_config = ConfigDict(extra='forbid')

    vendor: str | None = None
    vendor_id: int | None = None
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
