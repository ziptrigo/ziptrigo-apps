"""Schemas for the share-link endpoints (`/api/qr/{id}/links`)."""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import BeforeValidator, ConfigDict, Field

from ..limits import MAX_RECIPIENTS_PER_LINK

_DATE_ONLY = re.compile(r'\d{4}-\d{2}-\d{2}')


def _date_or_datetime(value: Any) -> Any:
    """A bare date ("2027-12-31") stays a date, which the service reads as the end of that day in
    the owner's zone; pydantic would otherwise read it as midnight at the *start* of the day.
    Anything else is left to pydantic as a date-time."""
    if isinstance(value, str) and _DATE_ONLY.fullmatch(value.strip()):
        return date.fromisoformat(value.strip())
    return value


ExpiresAt = Annotated[
    datetime | date | None,
    BeforeValidator(_date_or_datetime),
    Field(
        description=(
            'A date ("2027-12-31"): the link stops working at the end of that day in the '
            "owner's time zone. Or an ISO 8601 date-time; one without a UTC offset is read in "
            "the owner's time zone. `null` means no expiry. Not in the past."
        ),
    ),
]


class QRCodeShareRecipientSchema(Schema):
    """A person a link was emailed to, and when they were last emailed (the resend cooldown is
    counted from it)."""

    id: UUID
    email: str
    last_sent_at: datetime | None


class QRCodeShareErrorSchema(Schema):
    """The body of a 400, 404 or 409: one line saying what was refused. A 400 reads `field:
    message` when the refusal belongs to a field of the request (`expires_at`, `label`, `emails`,
    `message`) and is the message alone otherwise."""

    detail: str


class QRCodeShareLinkSchema(Schema):
    """One share link of a QR code. `url` is the address to hand out (`<site>/q/<code>`); anyone
    who has it sees the QR code as it is now and can download it. `label` is the owner's own
    reminder of who it is for, never shown publicly. `active` means not revoked and not expired;
    the link *works* only while its QR code is also not deleted or taken down. `state` is `revoked`,
    `expired` or `active` (the same fact as `active` and `revoked_at`, ready to show). A revoked link is
    never active again. `recipients` are the people it was emailed to, oldest first."""

    id: UUID
    url: str
    label: str
    expires_at: datetime | None
    revoked_at: datetime | None
    active: bool
    state: Literal['active', 'expired', 'revoked']
    recipients: list[QRCodeShareRecipientSchema]
    created_at: datetime


class QRCodeShareLinkCreateSchema(Schema):
    """Body for `POST /{qr_id}/links`. At most 10 links per QR code are active at once."""

    model_config = ConfigDict(extra='forbid')

    label: str = ''
    expires_at: ExpiresAt = None


class QRCodeShareLinkUpdateSchema(Schema):
    """Body for `PATCH /{qr_id}/links/{link_id}`: only the fields present are applied. An explicit
    `null` clears `expires_at` and `label`. A revoked link cannot be changed (a 400); extending an
    expired one makes it active again."""

    model_config = ConfigDict(extra='forbid')

    label: str | None = None
    expires_at: ExpiresAt = None


class QRCodeShareEmailSchema(Schema):
    """Body for `POST /{qr_id}/links/{link_id}/email`: the addresses to email the link to (at most
    20 people in all per link) and an optional message of up to 500 characters."""

    model_config = ConfigDict(extra='forbid')

    emails: list[Annotated[str, Field(max_length=320)]] = Field(
        min_length=1, max_length=MAX_RECIPIENTS_PER_LINK * 5
    )
    message: str = ''
