"""Schemas for the shared event endpoints of `/api/time/`."""

from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict


class WorldTimeEventLocationSchema(Schema):
    """An event's time in one of the places it was shared with (a copy made when the event was
    created). `starts_local` and `ends_local` are that place's wall clock (no offset);
    `utc_offset_minutes` is its offset at the start and `ends_utc_offset_minutes` at the end (they
    differ when its clocks change during the event)."""

    name: str
    detail: str
    timezone: str
    home: bool
    starts_local: str
    ends_local: str
    utc_offset_minutes: int
    ends_utc_offset_minutes: int


class WorldTimeEventSchema(Schema):
    """A shared event. `url` is its public page (anyone who has it sees the title, the time and
    the places); `ended` is whether it is over (it is deleted some days later)."""

    id: UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    url: str
    ended: bool
    locations: list[WorldTimeEventLocationSchema]
    created_at: datetime
    updated_at: datetime


class WorldTimeEventCreateSchema(Schema):
    """Body for `POST /events`: the tab whose locations the event is shared with, a title (1 to 120
    characters) and the two instants (ISO 8601 with an offset). A `tab` that isn't the caller's is a
    400."""

    model_config = ConfigDict(extra='forbid')

    tab: UUID
    title: str
    starts_at: datetime
    ends_at: datetime


class WorldTimeEventUpdateSchema(Schema):
    """Body for `PATCH /events/{id}`: only the fields present are applied. A new time needs both
    `starts_at` and `ends_at`. The link and the places never change."""

    model_config = ConfigDict(extra='forbid')

    title: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
