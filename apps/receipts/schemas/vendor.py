"""Schemas for the vendor endpoints (`/api/receipts/vendors`)."""

from datetime import date, datetime

from ninja import Schema
from pydantic import ConfigDict, Field

#: The most vendors one `POST /vendors/merge` takes besides `keep` (more is a 422). A merge is one
#: transaction locking every vendor in it, so a client can't make it arbitrarily large.
MAX_MERGE_IDS = 100


class VendorSchema(Schema):
    """One of the caller's vendors, as every vendor endpoint returns it.

    `vetted` says the user confirmed it (`vetted_at` is when); an unvetted vendor is only what the
    AI read. `aliases` are the other names it is known by (the spellings the AI read, its old names
    after a rename, the names of vendors merged into it), names only, oldest first; a receipt read
    under one of them is filed under this vendor.

    `receipt_count` counts the **live** receipts filed under it, `trashed_count` the ones in the
    Trash (the Vendors page shows both, as "N" and "+N in Trash"); a vendor can only be deleted
    when both are 0. `last_receipt_date` is the latest `receipt_date` among the live ones.
    """

    id: int
    name: str
    vetted: bool
    vetted_at: datetime | None
    aliases: list[str]
    receipt_count: int
    trashed_count: int
    last_receipt_date: date | None

    @staticmethod
    def resolve_aliases(obj) -> list[str]:
        return list(getattr(obj, 'also_read_as', []))


class VendorListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[VendorSchema]


class VendorUpdateSchema(Schema):
    """Body for `PATCH /vendors/{id}`; only the fields present are applied.

    `name` renames the vendor: every receipt filed under it shows the new name, and the old name
    stays one it is known by. Renaming is a confirmation, so the vendor ends up vetted unless the
    same request says `"vetted": false`. `vetted` alone vets (or un-vets) the vendor.

    An explicit `null` means **unchanged**, like leaving the field out (as in `PATCH /inbox`). A key
    that is not one of these fields is refused with a 422 rather than ignored.
    """

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    vetted: bool | None = None


class VendorMergeSchema(Schema):
    """Body for `POST /vendors/merge`: the vendor to keep and the vendors to merge into it (at most
    `MAX_MERGE_IDS`, 100; a longer list is a 422)."""

    keep: int
    merge: list[int] = Field(max_length=MAX_MERGE_IDS)


class VendorConflictSchema(Schema):
    """The 409 body of a rename that another vendor's name (or an equivalent one: same key, so
    `Acme LLC` clashes with `ACME`) already takes: say which, so the client can offer to merge."""

    detail: str
    conflicting_vendor_id: int
    conflicting_vendor_name: str
