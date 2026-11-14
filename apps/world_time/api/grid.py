"""Reading a tab's times: the grid of a day, and every location at one instant. Read-only, and
no query beyond loading the tab's rows, so neither is rate limited."""

from datetime import date, datetime
from uuid import UUID

from django.utils import timezone
from ninja.errors import HttpError

from apps.core.datetime_format import PUBLIC

from .. import services
from ..schemas import WorldTimeGridSchema, WorldTimeMomentSchema
from ..services import zones
from ..services.grid import HOUR, home_of
from .common import location_data, owned_tab, wall_clock
from .router import router


def _offset_minutes(moment: datetime) -> int:
    offset = moment.utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


@router.get('/tabs/{tab_id}/grid', response=WorldTimeGridSchema)
def grid(request, tab_id: UUID, date: str = ''):
    """The grid of a tab for `date` (`YYYY-MM-DD`; default: today on the home row's clock; a date
    more than ten years away is a 400): one tile per hour of **the home row's** calendar day, in
    every location's own clock, with the daylight-saving rules of that date."""
    tab = owned_tab(request, tab_id)
    locations = services.list_locations(tab)
    now = timezone.now()
    today = services.home_today(locations, now)
    day = today
    if date.strip():
        try:
            day = _parse_day(date.strip())
        except ValueError:
            raise HttpError(400, 'date must be a date like 2026-10-09.') from None
        if services.parse_day(day.isoformat(), today) != day:
            raise HttpError(400, 'date must be within ten years of today.')
    built = services.build_grid(locations, day, now=now, formats=PUBLIC)
    home_offset = built.rows[0].offsets[0] if built.rows else 0
    for row in built.rows:
        if row.location.is_home:
            home_offset = row.offsets[0]
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


def _parse_day(value: str) -> date:
    return date.fromisoformat(value)


@router.get('/tabs/{tab_id}/times', response=WorldTimeMomentSchema)
def times(request, tab_id: UUID, at: str = ''):
    """Every location of a tab at one instant: `at` (ISO 8601 with an offset, or without one to
    mean the home row's wall clock; default: now)."""
    tab = owned_tab(request, tab_id)
    locations = services.list_locations(tab)
    home = home_of(locations)
    home_zone = zones.tz(home.timezone if home else zones.UTC)
    moment = timezone.now()
    if at.strip():
        try:
            moment = datetime.fromisoformat(at.strip())
        except ValueError:
            raise HttpError(400, 'at must be a date and time like 2026-10-09T15:00.') from None
        if timezone.is_naive(moment):
            moment = moment.replace(tzinfo=home_zone)
    home_offset = _offset_minutes(moment.astimezone(home_zone))
    rows = []
    for location in locations:
        local = moment.astimezone(zones.tz(location.timezone))
        offset = _offset_minutes(local)
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
