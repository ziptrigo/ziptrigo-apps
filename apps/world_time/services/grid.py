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
    steps: list[list[int]]
    """`[minute, offset]` pairs for the script: the UTC offset in force from that minute of the
    day shown (the first is minute 0). Exact to the minute, so a clock change that falls *inside*
    a tile (a row under a home zone off the hour) is not read with the tile's first offset."""
    summary: str
    """For screen readers: the local time at which the day shown starts in this row."""
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
    minutes: int = 24 * 60
    """The real length of the day shown (1,470 on a 24.5-hour day, whose last tile runs past it)."""
    refresh_at: datetime | None = None
    """When a page left open should load again: the end of the day shown when that is today, or
    the next clock change of a row, whichever is first."""

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
            'minutes': self.minutes,
            'refreshAt': int(self.refresh_at.timestamp() * 1000) if self.refresh_at else 0,
            'rows': [
                {
                    'id': str(row.location.pk),
                    'title': row.location.title,
                    'steps': row.steps,
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


def offset_minutes(moment: datetime) -> int:
    """The UTC offset of an aware datetime, in minutes."""
    return _offset_minutes(moment)


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


def _tile(instant: datetime, zone, formats: DisplayFormats, today: date) -> Tile:
    local = instant.astimezone(zone)
    hour, minute = local.hour, local.minute
    # A new local day starts on the first tile whose date differs from the hour before. Not "the
    # hour is 0": where the clocks change at midnight (Havana, Santiago) hour 0 happens twice or
    # not at all.
    day_start = local.date() != (instant - HOUR).astimezone(zone).date()
    if formats.clock == '24h':
        clock, half = (f'{hour}:{minute:02d}' if minute else str(hour)), ''
    else:
        shown = hour % 12 or 12
        clock = f'{shown}:{minute:02d}' if minute else str(shown)
        half = 'am' if hour < 12 else 'pm'
    if day_start:
        # The weekday over the date; in a zone off the hour the time stays (`Tue` over `12:30`),
        # since it is not midnight, and the date is in the tooltip.
        main = WEEKDAY_ABBR[local.weekday()]
        sub = clock if minute else _short_date(local.date(), formats)
    else:
        main, sub = clock, half
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


def offset_steps(zone, start: datetime, minutes: int) -> list[list[int]]:
    """`[minute, offset]` for `zone` over the `minutes` after `start`: the offset at minute 0 and
    one entry per clock change, at the exact minute it happens. Hours are compared first and only
    an hour that holds a change is walked minute by minute."""
    steps = [[0, _offset_minutes(start.astimezone(zone))]]
    for hour_start in range(0, minutes, 60):
        hour_end = min(hour_start + 60, minutes)
        if _offset_minutes((start + timedelta(minutes=hour_end)).astimezone(zone)) == steps[-1][1]:
            continue
        for minute in range(hour_start + 1, hour_end + 1):
            offset = _offset_minutes((start + timedelta(minutes=minute)).astimezone(zone))
            if offset != steps[-1][1]:
                steps.append([minute, offset])
    return steps


def next_change(zone, now: datetime, days: int) -> tuple[datetime, int] | None:
    """The instant (to the minute) of `zone`'s next clock change within `days` days of `now`, and
    the change in minutes (positive: clocks go forward); None when there is none. Found by walking
    the hours ahead: `zoneinfo` has no list of transitions."""
    base = now.replace(second=0, microsecond=0)
    current = _offset_minutes(base.astimezone(zone))
    for step in range(1, days * 24 + 1):
        if _offset_minutes((base + step * HOUR).astimezone(zone)) == current:
            continue
        hour_start = base + (step - 1) * HOUR
        for minute in range(1, 61):
            moment = hour_start + timedelta(minutes=minute)
            offset = _offset_minutes(moment.astimezone(zone))
            if offset != current:
                return moment, offset - current
    return None


def dst_note(zone, now: datetime, formats: DisplayFormats, today: date) -> str:
    """`Clocks go back 1 hour on Sun 11/01/2026` when `zone`'s clocks change within the next
    `DST_WARNING_DAYS` days, else empty. The date is the local one just after the change."""
    found = next_change(zone, now, DST_WARNING_DAYS)
    if found is None:
        return ''
    moment, change = found
    amount = offset_text(abs(change))[1:]
    unit = 'hour' if abs(change) == 60 else ('hours' if abs(change) % 60 == 0 else 'h')
    direction = 'forward' if change > 0 else 'back'
    when = format_date(moment.astimezone(zone).date(), 'weekday', formats, today=today)
    return f'Clocks go {direction} {amount} {unit} on {when}'


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
    minutes = max(60, int((end - start).total_seconds() // 60))
    hours = max(1, ceil((end - start) / HOUR))
    instants = [start + index * HOUR for index in range(hours + 1)]
    grid.start, grid.hours, grid.minutes = start, hours, minutes
    home_offset = _offset_minutes(start.astimezone(home_zone))

    # Per zone, not per row: two rows of one zone share the walk ahead.
    changes: dict[str, tuple[datetime, int] | None] = {}
    refresh = [end] if grid.is_today else []
    for location in locations:
        zone = zones.tz(location.timezone)
        locals_ = [instant.astimezone(zone) for instant in instants]
        offsets = [_offset_minutes(local) for local in locals_]
        here = now.astimezone(zone)
        if location.timezone not in changes:
            change = next_change(zone, now, DST_WARNING_DAYS)
            changes[location.timezone] = change
            if change is not None:
                refresh.append(change[0])
        tiles = [_tile(instant, zone, formats, here.date()) for instant in instants[:-1]]
        grid.rows.append(
            Row(
                location=location,
                tiles=tiles,
                offsets=offsets,
                steps=offset_steps(zone, start, hours * 60),
                summary=f'The day shown starts at {tiles[0].title} here.',
                now_offset=_offset_minutes(here),
                zone_name=locals_[0].tzname() or '',
                utc_text=offset_text(offsets[0], prefix='UTC'),
                diff_text='' if location.pk == home.pk else offset_text(offsets[0] - home_offset),
                clock_text=format_time(here.replace(tzinfo=None).time(), formats),
                date_text=format_date(here.date(), 'weekday', formats, today=here.date()),
                dst_note=dst_note(zone, now, formats, here.date())
                if changes[location.timezone] is not None
                else '',
            )
        )
    grid.refresh_at = min(refresh) if refresh else None
    return grid
