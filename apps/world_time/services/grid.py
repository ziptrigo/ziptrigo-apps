"""The grid: for one day of the home row, what hour it is in every location.

The day shown is the **home location's** calendar day, midnight to midnight on its wall clock
(23 or 25 hours on a day its clocks change), cut into one-hour tiles. Every other row shows the
same instants on its own clock, so a column is one moment everywhere. Everything is computed here
with `zoneinfo` (the daylight-saving rules of the *chosen date*, not today's); the browser gets,
per row, the UTC offset of each tile's instant and only does arithmetic with them, so the page and
the server can never disagree about a zone.

Pure functions of their arguments (`now` is passed in): no queries.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from math import ceil

from apps.core.datetime_format import DMY, DisplayFormats, format_date, format_time, get_active

from ..limits import MAX_DATE_DISTANCE_DAYS
from ..models import Location
from . import zones

HOUR = timedelta(hours=1)
#: How far ahead a clock change is announced on a row.
DST_WARNING_DAYS = 7
#: Local hours `[start, end)` shaded as the working day, and as night; the rest is the early
#: morning and the evening.
DAY_HOURS = (8, 18)
NIGHT_HOURS = (22, 6)
WEEKDAY_ABBR = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
MONTH_ABBR = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def band_of(hour: int) -> str:
    """`day` (8 to 18), `night` (22 to 6) or `edge` (early morning, evening) for a local hour."""
    if DAY_HOURS[0] <= hour < DAY_HOURS[1]:
        return 'day'
    if hour >= NIGHT_HOURS[0] or hour < NIGHT_HOURS[1]:
        return 'night'
    return 'edge'


@dataclass(frozen=True, slots=True)
class Tile:
    """One hour of one row."""

    main: str
    """The hour (`3`, `15`, `3:30` in a zone off the hour); the weekday (`Fri`) on the tile where
    a new local day starts."""
    sub: str
    """`am` / `pm` under the hour (empty on a 24-hour clock); the date (`11/20`) on a day-start
    tile."""
    band: str
    weekend: bool
    day_start: bool
    tight: bool
    """The hour has minutes (`5:45`, a zone off the hour): drawn smaller so it fits the tile."""
    title: str
    """The full local date and time, for the tooltip and screen readers."""


@dataclass(slots=True)
class Row:
    location: Location
    tiles: list[Tile]
    offsets: list[int]
    """UTC offset in minutes at each tile's start, plus one for the end of the last tile."""
    now_offset: int
    """UTC offset in minutes right now (for the live clock, whatever date is shown)."""
    zone_name: str
    """The zone's abbreviation at the start of the day shown (`WET`, `GMT+5:30`)."""
    utc_text: str
    """`UTC+1`, `UTC-3:30`."""
    diff_text: str
    """Hours from the home row at the start of the day shown: `+5`, `-3:30`; empty for home."""
    clock_text: str
    date_text: str
    dst_note: str = ''

    @property
    def zone_title(self) -> str:
        """The tooltip of the offset: the zone's name, abbreviation and UTC offset."""
        return f'{self.location.timezone.replace("_", " ")} · {self.zone_name} · {self.utc_text}'


@dataclass(slots=True)
class Grid:
    day: date
    today: date
    rows: list[Row] = field(default_factory=list)
    start: datetime | None = None
    hours: int = 24

    @property
    def is_today(self) -> bool:
        return self.day == self.today

    @property
    def previous_day(self) -> date:
        return self.day - timedelta(days=1)

    @property
    def next_day(self) -> date:
        return self.day + timedelta(days=1)

    @property
    def day_iso(self) -> str:
        return self.day.isoformat()

    @property
    def previous_iso(self) -> str:
        return self.previous_day.isoformat()

    @property
    def next_iso(self) -> str:
        return self.next_day.isoformat()

    @property
    def payload(self) -> dict:
        """What the page's script needs: the first instant, the number of tiles, and each row's
        offsets."""
        return {
            'start': int(self.start.timestamp() * 1000) if self.start else 0,
            'hours': self.hours,
            'rows': [
                {
                    'id': str(row.location.pk),
                    'title': row.location.title,
                    'offsets': row.offsets,
                    'nowOffset': row.now_offset,
                    'home': row.location.is_home,
                }
                for row in self.rows
            ],
        }


def parse_day(value: str | None, today: date) -> date:
    """The `?date=` of a request: an ISO date within ten years of today, else today."""
    try:
        day = date.fromisoformat(value or '')
    except ValueError:
        return today
    if abs((day - today).days) > MAX_DATE_DISTANCE_DAYS:
        return today
    return day


def home_of(locations: list[Location]) -> Location | None:
    """The tab's home row: the one marked, else the first (a tab always has one when it has rows;
    this only guards a row caught between two writes)."""
    return next((row for row in locations if row.is_home), locations[0] if locations else None)


def home_today(locations: list[Location], now: datetime) -> date:
    """Today's date on the home row's clock (UTC for an empty tab)."""
    home = home_of(locations)
    return now.astimezone(zones.tz(home.timezone if home else zones.UTC)).date()


def _offset_minutes(moment: datetime) -> int:
    offset = moment.utcoffset() or timedelta(0)
    return int(offset.total_seconds() // 60)


def offset_text(minutes: int, *, prefix: str = '') -> str:
    """`+5`, `-3:30`, `+0` (with `prefix`: `UTC+5`)."""
    sign = '-' if minutes < 0 else '+'
    hours, rest = divmod(abs(minutes), 60)
    return f'{prefix}{sign}{hours}' + (f':{rest:02d}' if rest else '')


def _short_date(day: date, formats: DisplayFormats) -> str:
    """A date small enough for a tile: `11/20`, `20/11`, `Nov 20`. Never a year."""
    if formats.is_public:
        return f'{MONTH_ABBR[day.month - 1]} {day.day}'
    first, second = (day.day, day.month) if formats.date_order == DMY else (day.month, day.day)
    return f'{first}/{second}'


def _tile(local: datetime, formats: DisplayFormats, today: date) -> Tile:
    hour, minute = local.hour, local.minute
    day_start = hour == 0
    if day_start:
        main, sub = WEEKDAY_ABBR[local.weekday()], _short_date(local.date(), formats)
    elif formats.clock == '24h':
        main, sub = (f'{hour}:{minute:02d}' if minute else str(hour)), ''
    else:
        shown = hour % 12 or 12
        main = f'{shown}:{minute:02d}' if minute else str(shown)
        sub = 'am' if hour < 12 else 'pm'
    naive = local.replace(tzinfo=None)
    return Tile(
        main=main,
        sub=sub,
        band=band_of(hour),
        weekend=local.weekday() >= 5,
        day_start=day_start,
        tight=':' in main,
        title=f'{format_date(naive, "weekday", formats, today=today)}, '
        f'{format_time(naive.time(), formats)}',
    )


def dst_note(zone, now: datetime, formats: DisplayFormats, today: date) -> str:
    """`Clocks go back 1 hour on Sun 11/01/2026` when `zone`'s clocks change within the next
    `DST_WARNING_DAYS` days, else empty. Found by walking the hours ahead: `zoneinfo` has no list
    of transitions."""
    current = _offset_minutes(now.astimezone(zone))
    for step in range(1, DST_WARNING_DAYS * 24 + 1):
        local = (now + step * HOUR).astimezone(zone)
        offset = _offset_minutes(local)
        if offset != current:
            change = offset - current
            amount = offset_text(abs(change))[1:]
            unit = 'hour' if abs(change) == 60 else ('hours' if abs(change) % 60 == 0 else 'h')
            direction = 'forward' if change > 0 else 'back'
            when = format_date(local.date(), 'weekday', formats, today=today)
            return f'Clocks go {direction} {amount} {unit} on {when}'
    return ''


def build_grid(
    locations: list[Location],
    day: date,
    *,
    now: datetime,
    formats: DisplayFormats | None = None,
) -> Grid:
    """The grid of `locations` (in their order) for the home row's calendar day `day`."""
    formats = formats or get_active()
    home = home_of(locations)
    home_zone = zones.tz(home.timezone) if home is not None else zones.tz(zones.UTC)
    today = now.astimezone(home_zone).date()
    grid = Grid(day=day, today=today)
    if home is None:
        return grid

    # Local midnight to the next local midnight: 23, 24 or 25 hours (24.5 on Lord Howe Island).
    start = datetime(day.year, day.month, day.day, tzinfo=home_zone).astimezone(UTC)
    following = day + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=home_zone).astimezone(UTC)
    hours = max(1, ceil((end - start) / HOUR))
    instants = [start + index * HOUR for index in range(hours + 1)]
    grid.start, grid.hours = start, hours
    home_offset = _offset_minutes(start.astimezone(home_zone))

    notes: dict[str, str] = {}
    for location in locations:
        zone = zones.tz(location.timezone)
        locals_ = [instant.astimezone(zone) for instant in instants]
        offsets = [_offset_minutes(local) for local in locals_]
        here = now.astimezone(zone)
        if location.timezone not in notes:
            notes[location.timezone] = dst_note(zone, now, formats, here.date())
        grid.rows.append(
            Row(
                location=location,
                tiles=[_tile(local, formats, here.date()) for local in locals_[:-1]],
                offsets=offsets,
                now_offset=_offset_minutes(here),
                zone_name=locals_[0].tzname() or '',
                utc_text=offset_text(offsets[0], prefix='UTC'),
                diff_text='' if location.pk == home.pk else offset_text(offsets[0] - home_offset),
                clock_text=format_time(here.replace(tzinfo=None).time(), formats),
                date_text=format_date(here.date(), 'weekday', formats, today=here.date()),
                dst_note=notes[location.timezone],
            )
        )
    return grid
