"""Schemas for the share-link endpoints (`/api/notes/notes/{id}/links`)."""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import BeforeValidator, ConfigDict, Field

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

LinkMode = Literal['read', 'edit']


class NoteLinkSchema(Schema):
    """One share link of a note. `url` is the address to hand out (`<site>/n/<code>`); `mode` is
    `read` (the note, read-only) or `edit` (anyone with the link can also change the note's
    content). `label` is the owner's own reminder of who it is for, never shown publicly. `active`
    means not revoked and not expired; the link *works* only while its note is also out of the
    Trash, not locked and not taken down. A revoked link is never active again."""

    id: UUID
    url: str
    mode: LinkMode
    label: str
    expires_at: datetime | None
    revoked_at: datetime | None
    active: bool
    created_at: datetime
    last_edited_at: datetime | None


class NoteLinkCreateSchema(Schema):
    """Body for `POST /notes/{id}/links`. At most 10 links per note are active at once."""

    model_config = ConfigDict(extra='forbid')

    mode: LinkMode = 'read'
    label: str = ''
    expires_at: ExpiresAt = None


class NoteLinkUpdateSchema(Schema):
    """Body for `PATCH /notes/{id}/links/{link_id}`: only the fields present are applied. An
    explicit `null` clears `expires_at` and `label`; `mode` cannot be null (a 400). A revoked link
    cannot be changed (a 400); extending an expired one makes it active again."""

    model_config = ConfigDict(extra='forbid')

    mode: LinkMode | None = None
    label: str | None = None
    expires_at: ExpiresAt = None
