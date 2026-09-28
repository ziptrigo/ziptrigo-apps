"""Schemas for the JWT API's transfer endpoints (`/api/ft/transfers/`, spec section 14): creating
a draft, finalizing (sending) it, listing, reading and updating one.
"""

from datetime import datetime
from uuid import UUID

from ninja import Schema

from ..services.expiry_choices import NO_EXPIRATION_CHOICE


class RecipientSchema(Schema):
    """One recipient on a sent transfer -- enough for a client (the CLI's `resend`) to resend that
    recipient's email without needing a separate lookup endpoint just to learn their id."""

    id: UUID
    email: str
    last_sent_at: datetime | None


class TransferFileSchema(Schema):
    """One file on a transfer -- enough for a client (the CLI's `send --draft-id` resume, or
    `filetransfer show`) to tell which of its local files are already uploaded, and by how much,
    without a separate endpoint."""

    id: UUID
    name: str
    size: int
    uploaded: bool
    client_last_modified: int | None = None


class TransferSchema(Schema):
    """One transfer, as returned by every read/write endpoint below. Mirrors the fields the
    dashboard shows (spec section 6), never the password itself."""

    id: UUID
    status: str
    display_name: str
    message: str
    recipients: list[RecipientSchema]
    files: list[TransferFileSchema]
    expires_at: datetime | None
    max_downloads: int | None
    downloads_remaining: int | None
    download_count: int
    has_password: bool
    notify_on_download: bool
    size_bytes: int
    credits_charged: int
    download_url: str
    created_at: datetime | None

    @staticmethod
    def resolve_recipients(obj) -> list:
        return list(obj.recipients.all())

    @staticmethod
    def resolve_files(obj) -> list:
        return list(obj.files.all())

    @staticmethod
    def resolve_has_password(obj) -> bool:
        return bool(obj.password_hash)

    @staticmethod
    def resolve_download_url(obj) -> str:
        return obj.absolute_download_url


class TransferListSchema(Schema):
    """A page of `TransferSchema` (spec section 14: "list transfers ... pagination")."""

    count: int
    limit: int
    offset: int
    results: list[TransferSchema]


class FinalizeTransferSchema(Schema):
    """Body for `POST /transfers/{id}/send`: the same options `SendOptionsForm` collects on the
    web send page, applied and validated by the same service (`services.finalize_send`)."""

    recipients: list[str]
    message: str = ''
    expiry_choice: str = NO_EXPIRATION_CHOICE
    expiry_date: datetime | None = None
    max_downloads: int | None = None
    password: str = ''
    notify_on_download: bool = True


class TransferUpdateSchema(Schema):
    """Body for `PATCH /transfers/{id}` (spec section 14: "update (disable, re-enable, expiry, max
    downloads, password, notify toggle)"). Every field is optional -- only the ones present in the
    request body are applied (`payload.dict(exclude_unset=True)` in the router), so omitting a
    field always means "leave it as it is", never "clear it". A blank `password` with
    `remove_password` unset is likewise a no-op; set `remove_password: true` to actually clear it,
    same as the dashboard's own settings form (see `forms.dashboard_actions.PasswordActionForm`).
    """

    disabled: bool | None = None
    expiry_choice: str | None = None
    expiry_date: datetime | None = None
    max_downloads: int | None = None
    password: str | None = None
    remove_password: bool = False
    notify_on_download: bool | None = None


class AddRecipientsSchema(Schema):
    recipients: list[str]
