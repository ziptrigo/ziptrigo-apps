"""Schemas for the JWT API's transfer endpoints (`/api/ft/transfers/`, spec section 14): creating
a draft, finalizing (sending) it, listing, reading and updating one.
"""

import re
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from ninja import Schema
from pydantic import BeforeValidator, Field

from ..services.expiry_choices import NO_EXPIRATION_CHOICE, end_of_day

_DATE_ONLY = re.compile(r'\d{4}-\d{2}-\d{2}')


def _date_only_to_end_of_day(value: Any) -> Any:
    """Read a bare date ("2027-12-31") as the end of that day, as the web date picker does.
    pydantic would otherwise parse it as midnight at the *start* of the day, so the link would
    stop working a day earlier than the sender meant. Anything with a time part passes through."""
    if isinstance(value, str) and _DATE_ONLY.fullmatch(value.strip()):
        return end_of_day(date.fromisoformat(value.strip()))
    if isinstance(value, date) and not isinstance(value, datetime):
        return end_of_day(value)
    return value


ExpiryDate = Annotated[
    datetime | None,
    BeforeValidator(_date_only_to_end_of_day),
    Field(
        description=(
            'The expiry for `expiry_choice: "custom"`. An ISO 8601 date-time; one without a '
            'UTC offset ("2027-12-31T18:00:00") is read in the server\'s time zone '
            '(settings.TIME_ZONE, UTC by default). A date alone ("2027-12-31") means the end '
            'of that day in that time zone, like the web date picker.'
        ),
    ),
]


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
    expiry_date: ExpiryDate = None
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
    expiry_date: ExpiryDate = None
    max_downloads: int | None = None
    password: str | None = None
    remove_password: bool = False
    notify_on_download: bool | None = None


class AddRecipientsSchema(Schema):
    recipients: list[str]
