"""Schemas for the tab, location, search, grid and settings endpoints of `/api/time/`.

Every class is prefixed `WorldTime`: the OpenAPI document keys a schema by its class name alone.
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from ..limits import MAX_REORDER_IDS


class WorldTimeLocationSchema(Schema):
    """One row of a tab. `title` is what the row is called (the caller's `label` when there is
    one, else `name`); `geoname_id` is null for a row added as a bare time zone; exactly one row of
    a tab that has any is `home`."""

    id: UUID
    title: str
    name: str
    label: str
    region: str
    country: str
    country_code: str
    timezone: str
    geoname_id: int | None
    home: bool
    position: int


class WorldTimeTabSchema(Schema):
    """One of the caller's tabs, as the list shows it."""

    id: UUID
    name: str
    position: int
    location_count: int


class WorldTimeTabDetailSchema(Schema):
    """A tab with its locations, in the caller's order."""

    id: UUID
    name: str
    position: int
    locations: list[WorldTimeLocationSchema]


class WorldTimeTabWriteSchema(Schema):
    """Body for `POST /tabs` and `PATCH /tabs/{id}`: the name (1 to 50 characters, unique among
    the caller's tabs ignoring case)."""

    model_config = ConfigDict(extra='forbid')

    name: str


class WorldTimeLocationCreateSchema(Schema):
    """Body for `POST /tabs/{id}/locations`: **either** `city` (a GeoNames id from `GET /search`)
    **or** `zone` (an IANA name such as `Europe/Lisbon`, or `UTC`), and optionally a `label`."""

    model_config = ConfigDict(extra='forbid')

    city: int | None = None
    zone: str | None = None
    label: str = ''


class WorldTimeLocationUpdateSchema(Schema):
    """Body for `PATCH /tabs/{id}/locations/{location_id}`: only the fields present are applied.
    `label` is the caller's own name for the row (`null` or empty clears it); `home: true` makes
    the row the tab's home (`false` is refused: choose another row instead)."""

    model_config = ConfigDict(extra='forbid')

    label: str | None = None
    home: bool | None = None


class WorldTimeOrderSchema(Schema):
    """Body for `POST /tabs/{id}/locations/reorder`: the location ids in the new order (ids that
    aren't the tab's are ignored; at most 500, a longer list is a 422)."""

    model_config = ConfigDict(extra='forbid')

    ids: Annotated[list[UUID], Field(max_length=MAX_REORDER_IDS)]


class WorldTimeSearchResultSchema(Schema):
    """A place the search offers. To add it, send `city_id` back as `city` (an integer; set when
    `kind` is `city`) or `value` as `zone` (when `kind` is `zone`). `value` is the same id or
    zone as a string. `hint` says why it matched when that is not obvious (`also
    Lisboa`, `Time zone`)."""

    kind: Literal['city', 'zone']
    value: str
    city_id: int | None
    name: str
    detail: str
    hint: str


class WorldTimeTileSchema(Schema):
    """One hour of one row of the grid. `local` is the row's wall clock at the tile's start
    (`2026-12-10T09:00`, no offset); `band` is `day` (8 to 18), `night` (22 to 6) or `edge`."""

    local: str
    band: Literal['day', 'edge', 'night']
    weekend: bool
    day_start: bool


class WorldTimeGridRowSchema(Schema):
    """A location's hours for the day shown. `steps` are `[minute, offset]` pairs: the UTC offset
    (minutes) in force from that minute of the day (the first is minute 0), exact to the minute, so
    a client can turn any instant of the day into this row's wall clock. `dst_note` is a sentence
    in the public wording when the row's clocks change within 7 days **of now** (not of the `date`
    shown), else empty."""

    location: WorldTimeLocationSchema
    zone_name: str
    utc_offset_minutes: int
    minutes_from_home: int
    now_offset_minutes: int
    steps: list[list[int]]
    dst_note: str
    tiles: list[WorldTimeTileSchema]


class WorldTimeGridSchema(Schema):
    """The grid of a tab for one calendar day **of its home row**: `start` is that day's local
    midnight as an instant, `minutes` its real length (1,380 or 1,500 on a day the home clocks
    change) and `hours` the number of tiles per row."""

    date: date
    today: date
    start: datetime | None
    hours: int
    minutes: int
    rows: list[WorldTimeGridRowSchema]


class WorldTimeMomentRowSchema(Schema):
    """One location at one instant: its wall clock (`local`, no offset), its UTC offset and how
    far it is from the home row."""

    location: WorldTimeLocationSchema
    local: str
    zone_name: str
    utc_offset_minutes: int
    minutes_from_home: int


class WorldTimeMomentSchema(Schema):
    """Every location of a tab at one instant: `at`, in UTC."""

    at: datetime
    rows: list[WorldTimeMomentRowSchema]


class WorldTimeSettingsSchema(Schema):
    """The caps (set by the site) and how much of them the caller uses. World Time is free."""

    max_tabs: int
    tabs: int
    max_locations_per_tab: int
    max_events: int
    events: int
    event_purge_days: int
    cities_loaded: bool
