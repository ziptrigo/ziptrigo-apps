"""Reading a tab's times: the grid of a day, and every location at one instant. Read-only, and
no query beyond loading the tab's rows, so neither is rate limited."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from django.utils import timezone
from ninja import Query
from ninja.errors import HttpError

from apps.core.datetime_format import PUBLIC

from .. import services
from ..limits import MAX_DATE_DISTANCE_DAYS
from ..schemas import WorldTimeGridSchema, WorldTimeMomentSchema
from ..services import zones
from ..services.grid import HOUR, home_of, offset_minutes
from .common import location_data, owned_tab, wall_clock
from .router import router

AT_FORMAT_MESSAGE = 'at must be a date and time like 2026-10-09T15:00.'
AT_RANGE_MESSAGE = 'at must be within ten years of now.'
AT_MISSING_MESSAGE = (
    "at is a time that doesn't exist on the home location's clock that day: the clocks go "
    'forward over it.'
)


@router.get('/tabs/{tab_id}/grid', response=WorldTimeGridSchema)
def grid(
    request,
    tab_id: UUID,
    day_text: Annotated[str, Query(alias='date')] = '',  # ty: ignore[call-non-callable]
):
    """The grid of a tab for `date` (`YYYY-MM-DD`; default: today on the home row's clock; a date
    more than ten years away is a 400): one tile per hour of **the home row's** calendar day, in
    every location's own clock, with the daylight-saving rules of that date."""
    tab = owned_tab(request, tab_id)
    locations = services.list_locations(tab)
    now = timezone.now()
    today = services.home_today(locations, now)
    day = today
    if day_text.strip():
        try:
            day = date.fromisoformat(day_text.strip())
        except ValueError:
            raise HttpError(400, 'date must be a date like 2026-10-09.') from None
        if services.parse_day(day.isoformat(), today) != day:
            raise HttpError(400, 'date must be within ten years of today.')
    built = services.build_grid(locations, day, now=now, formats=PUBLIC)
    home_offset = next(
        (row.offsets[0] for row in built.rows if row.location.is_home),
        built.rows[0].offsets[0] if built.rows else 0,
    )
    start = built.start
    instants = [start + index * HOUR for index in range(built.hours)] if start else []
    rows = []
    for row in built.rows:
        zone = zones.tz(row.location.timezone)
        rows.append(
            {
                'location': location_data(row.location),
                'zone_name': row.zone_name,
                'utc_offset_minutes': row.offsets[0],
                'minutes_from_home': row.offsets[0] - home_offset,
                'now_offset_minutes': row.now_offset,
                'steps': row.steps,
                'dst_note': row.dst_note,
                'tiles': [
                    {
                        'local': wall_clock(instant.astimezone(zone)),
                        'band': tile.band,
                        'weekend': tile.weekend,
                        'day_start': tile.day_start,
                    }
                    for instant, tile in zip(instants, row.tiles, strict=True)
                ],
            }
        )
    return {
        'date': built.day,
        'today': built.today,
        'start': built.start,
        'hours': built.hours,
        'minutes': built.minutes,
        'rows': rows,
    }


def _moment(at: str, home_zone, now: datetime) -> datetime:
    """The instant `at` names, in UTC. With an offset it is that instant; without one it is read
    on the home row's wall clock (a time the clocks skip that day is refused; one that happens
    twice is its first occurrence). Anything more than ten years from now is refused, which also
    keeps the conversions below inside the calendar."""
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError:
        raise HttpError(400, AT_FORMAT_MESSAGE) from None
    naive = timezone.is_naive(parsed)
    reference = now.astimezone(home_zone).replace(tzinfo=None) if naive else now
    try:
        distance = abs(parsed - reference)
    except OverflowError:
        raise HttpError(400, AT_RANGE_MESSAGE) from None
    if distance > timedelta(days=MAX_DATE_DISTANCE_DAYS):
        raise HttpError(400, AT_RANGE_MESSAGE)
    if not naive:
        return parsed.astimezone(UTC)
    moment = parsed.replace(tzinfo=home_zone).astimezone(UTC)
    if wall_clock(moment.astimezone(home_zone)) != wall_clock(parsed):
        raise HttpError(400, AT_MISSING_MESSAGE)
    return moment


@router.get('/tabs/{tab_id}/times', response=WorldTimeMomentSchema)
def times(request, tab_id: UUID, at: str = ''):
    """Every location of a tab at one instant: `at`, ISO 8601 (default: now; a date alone means
    midnight; within ten years of now). With an offset (`2026-10-09T15:00:00Z`,
    `...+02:00`: percent-encode the `+` in a query string, where a raw one is a space) it is
    that instant; without one it is read on the **home row's wall clock** (a time its clocks skip
    that day is a 400; one that happens twice is its first occurrence). The answer's `at` is the
    instant in UTC."""
    tab = owned_tab(request, tab_id)
    locations = services.list_locations(tab)
    home = home_of(locations)
    home_zone = zones.tz(home.timezone if home else zones.UTC)
    now = timezone.now()
    moment = _moment(at.strip(), home_zone, now) if at.strip() else now.astimezone(UTC)
    home_offset = offset_minutes(moment.astimezone(home_zone))
    rows = []
    for location in locations:
        local = moment.astimezone(zones.tz(location.timezone))
        offset = offset_minutes(local)
        rows.append(
            {
                'location': location_data(location),
                'local': wall_clock(local),
                'zone_name': local.tzname() or '',
                'utc_offset_minutes': offset,
                'minutes_from_home': offset - home_offset,
            }
        )
    return {'at': moment, 'rows': rows}
