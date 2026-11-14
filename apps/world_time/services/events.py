"""Shared events (issue #269, PR 2): a time range the owner picked on the grid, given a title and a
public link, `/w/<code>`.

An event stores two instants and a **snapshot** of the tab's locations, so the link keeps showing
what its owner shared whatever happens to the tab. It is the owner's to edit (the link survives)
and to delete; there is no report form and no takedown (the owner's decision: a title and a time
are all it carries). `purge_ended` (the daily `world_time.purge_events` job) deletes events
`event_purge_days` after they end. A link that doesn't work -- unknown, deleted, purged -- is one
neutral page that never says which.

Writes take the account lock first, like every counting write of the app. Refusals are
`ValidationError`s keyed by field (`__all__` for none).
"""

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.datetime_format import DisplayFormats, format_date, format_time, get_active
from apps.core.services.qr_image import render_png

from ..limits import EVENT_MAX_DAYS, EVENT_TITLE_MAX, MAX_DATE_DISTANCE_DAYS
from ..models import Event, Tab, WorldTimeSettings
from . import zones
from .accounts import get_account, lock_account
from .grid import offset_text
from .locations import list_locations
from .text import clean_line

TOO_MANY_MESSAGE = 'You have too many events. Delete one first.'
GONE_MESSAGE = 'This event no longer exists.'
EMPTY_TAB_MESSAGE = 'Add a location to the tab first.'
_CODE_BYTES = 16
_PURGE_BATCH = 500
QR_SCALE = 8
QR_BORDER = 2


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def clean_title(title: object) -> str:
    cleaned = clean_line(title)
    if not cleaned:
        raise _error('title', 'Give the event a title.')
    if len(cleaned) > EVENT_TITLE_MAX:
        raise _error('title', f'Keep the title to {EVENT_TITLE_MAX} characters or fewer.')
    return cleaned


PAST_MESSAGE = 'That time has already passed. Choose one that has not ended yet.'


def clean_times(starts_at: object, ends_at: object) -> tuple[datetime, datetime]:
    """The two instants of an event, checked and returned **in UTC**: both aware, the end after
    the start, at most `EVENT_MAX_DAYS` long, not already over, and within ten years of now.

    Everything is judged on instants. Two datetimes that share one `ZoneInfo` are compared and
    subtracted by Python on the *wall clock*, ignoring the offset, so across a clock change an end
    could pass as "after" a start it precedes: hence the conversion first.

    A range that has ended is refused: its link would be deleted by the purge soon after (or at
    once), with nobody told."""
    if not isinstance(starts_at, datetime) or timezone.is_naive(starts_at):
        raise _error('starts_at', 'Choose when the event starts.')
    if not isinstance(ends_at, datetime) or timezone.is_naive(ends_at):
        raise _error('ends_at', 'Choose when the event ends.')
    starts_at, ends_at = starts_at.astimezone(UTC), ends_at.astimezone(UTC)
    if ends_at <= starts_at:
        raise _error('ends_at', 'The end must be after the start.')
    if ends_at - starts_at > timedelta(days=EVENT_MAX_DAYS):
        raise _error('ends_at', f'An event can last at most {EVENT_MAX_DAYS} days.')
    now = timezone.now()
    if ends_at <= now:
        raise _error('ends_at', PAST_MESSAGE)
    if starts_at - now > timedelta(days=MAX_DATE_DISTANCE_DAYS):
        raise _error('starts_at', 'Choose a date within ten years of today.')
    return starts_at, ends_at


def snapshot(tab: Tab) -> list[dict]:
    """What an event keeps of a tab, and so **what anyone with the link sees**: per location, the
    name shown (the user's own label when there is one), the place, the zone and whether it is the
    home row (the public page does not mark it)."""
    rows = []
    for location in list_locations(tab):
        place = ', '.join(part for part in (location.name, location.detail) if part)
        rows.append(
            {
                'name': location.title,
                'detail': place if location.label else location.detail,
                'timezone': location.timezone,
                'home': location.is_home,
            }
        )
    return rows


def _new_code() -> str:
    return secrets.token_urlsafe(_CODE_BYTES)


def create_event(
    user: User, tab: Tab, *, title: str, starts_at: datetime, ends_at: datetime
) -> Event:
    """Share a time range of `tab`: a new event with a fresh link."""
    cleaned = clean_title(title)
    starts_at, ends_at = clean_times(starts_at, ends_at)
    get_account(user)
    with transaction.atomic():
        lock_account(user.pk)
        if Event.objects.filter(user=user).count() >= WorldTimeSettings.load().max_events_per_user:
            raise _error('__all__', TOO_MANY_MESSAGE)
        rows = snapshot(tab)
        if not rows:
            raise _error('__all__', EMPTY_TAB_MESSAGE)
        for _ in range(5):
            try:
                with transaction.atomic():
                    return Event.objects.create(
                        user=user,
                        code=_new_code(),
                        title=cleaned,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        locations=rows,
                    )
            except IntegrityError:  # 128 random bits collided: it never will, but try again
                continue
        raise _error('__all__', 'Could not make a link. Try again.')


def update_event(
    event: Event,
    *,
    title: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> Event:
    """Change the title and/or the time (`None` leaves a field; a new time needs both ends). The
    link and the locations stay as they are."""
    new_title = clean_title(title) if title is not None else None
    if (starts_at is None) != (ends_at is None):
        raise _error('ends_at', 'Send both the start and the end.')
    times = clean_times(starts_at, ends_at) if starts_at is not None else None
    get_account(event.user)
    with transaction.atomic():
        lock_account(event.user_id)
        fresh = Event.objects.filter(pk=event.pk).first()
        if fresh is None:
            raise _error('__all__', GONE_MESSAGE)
        fields = ['updated_at']
        if new_title is not None:
            fresh.title = new_title
            fields.append('title')
        if times is not None:
            fresh.starts_at, fresh.ends_at = times
            fields += ['starts_at', 'ends_at']
        fresh.save(update_fields=fields)
    event.title, event.starts_at, event.ends_at = fresh.title, fresh.starts_at, fresh.ends_at
    event.updated_at = fresh.updated_at
    return event


def delete_event(event: Event) -> None:
    """Delete the event: its link shows the neutral page from now on. Idempotent."""
    Event.objects.filter(pk=event.pk).delete()


def list_events(user: User, *, now: datetime | None = None) -> list[Event]:
    """The user's events: those that have not ended, soonest first, then the ended ones, latest
    first. Each gets `.ended`."""
    now = now or timezone.now()
    events = list(Event.objects.filter(user=user))
    for event in events:
        event.ended = event.ends_at <= now
    ahead = sorted((e for e in events if e.ends_at > now), key=lambda e: (e.starts_at, e.pk))
    past = sorted((e for e in events if e.ends_at <= now), key=lambda e: e.starts_at, reverse=True)
    return ahead + past


def get_available(code: str) -> Event | None:
    """The event a public code opens, or None for every reason alike (unknown, deleted, purged)."""
    if not code or len(code) > 32:
        return None
    return Event.objects.filter(code=code).first()


def event_path(event: Event) -> str:
    return f'/w/{event.code}'


def event_url(event: Event) -> str:
    """The public address of an event: `<BASE_URL>/w/<code>` (never read from a request)."""
    return f'{settings.BASE_URL.rstrip("/")}{event_path(event)}'


@lru_cache(maxsize=256)
def qr_data_uri(url: str) -> str:
    """The `data:` URI of a black-on-white PNG QR code of `url`, rendered in memory and stored
    nowhere. Cached per URL: a code never changes what it encodes."""
    png = render_png(url, scale=QR_SCALE, border=QR_BORDER)
    return f'data:image/png;base64,{base64.b64encode(png).decode("ascii")}'


def purge_ended(now: datetime | None = None) -> int:
    """Delete the events that ended more than `event_purge_days` ago, in batches. Returns how
    many. Idempotent."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=WorldTimeSettings.load().event_purge_days)
    deleted = 0
    while True:
        # Two queries per batch; the delete repeats the cutoff, so an event edited to a later end
        # between them is spared.
        ids = list(
            Event.objects.filter(ends_at__lt=cutoff).values_list('pk', flat=True)[:_PURGE_BATCH]
        )
        if not ids:
            return deleted
        deleted += Event.objects.filter(pk__in=ids, ends_at__lt=cutoff).delete()[0]


# -- Showing an event ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventRow:
    """An event's time in one of its locations."""

    name: str
    detail: str
    home: bool
    text: str
    """`Thu 12/10/2026, 9:00 AM – 10:00 AM`, with both dates when it crosses a midnight."""
    zone_label: str
    """`WET · UTC+0`."""


def range_text(start: datetime, end: datetime, formats: DisplayFormats | None = None) -> str:
    """Two local (naive or same-zone) datetimes as `date, time – time`, the end's date too when it
    is another day."""
    formats = formats or get_active()
    first, last = start.replace(tzinfo=None), end.replace(tzinfo=None)
    today = first.date()
    text = f'{format_date(first, "weekday", formats, today=today)}, {format_time(first.time(), formats)}'
    text += ' \u2013 '
    if last.date() != first.date():
        text += f'{format_date(last, "weekday", formats, today=today)}, '
    return text + format_time(last.time(), formats)


def _snapshot_rows(event: Event) -> list[dict]:
    """The stored locations, tolerating a row that is not what `snapshot` writes."""
    rows = event.locations if isinstance(event.locations, list) else []
    return [row for row in rows if isinstance(row, dict) and row.get('timezone')]


def home_zone_name(event: Event) -> str:
    """The zone the owner picked the time in: the snapshot's home row, else its first, else UTC."""
    rows = _snapshot_rows(event)
    home = next((row for row in rows if row.get('home')), rows[0] if rows else None)
    return str(home['timezone']) if home else zones.UTC


def home_name(event: Event) -> str:
    rows = _snapshot_rows(event)
    home = next((row for row in rows if row.get('home')), rows[0] if rows else None)
    return str(home.get('name') or zones.place_name(str(home['timezone']))) if home else zones.UTC


def _zone_label(moment: datetime) -> str:
    """`WET · UTC+0`; only `UTC+3` for a zone with no abbreviation (its `tzname()` is the offset
    again, `+03`)."""
    offset = int((moment.utcoffset() or timedelta(0)).total_seconds() // 60)
    name = moment.tzname() or ''
    utc = offset_text(offset, prefix='UTC')
    return utc if not name or name[0] in '+-' else f'{name} \u00b7 {utc}'


def event_rows(event: Event, formats: DisplayFormats | None = None) -> list[EventRow]:
    """The event's time in every location of its snapshot, in the given wording. Where a
    location's clocks change during the event, the label names both offsets."""
    rows = []
    for row in _snapshot_rows(event):
        zone = zones.tz(str(row['timezone']))
        start, end = event.starts_at.astimezone(zone), event.ends_at.astimezone(zone)
        label = _zone_label(start)
        if end.utcoffset() != start.utcoffset():
            label = f'{label}, then {_zone_label(end)}'
        rows.append(
            EventRow(
                name=str(row.get('name') or zones.place_name(str(row['timezone']))),
                detail=str(row.get('detail') or ''),
                home=bool(row.get('home')),
                text=range_text(start, end, formats),
                zone_label=label,
            )
        )
    return rows


def home_range_text(event: Event, formats: DisplayFormats | None = None) -> str:
    """The event's time on its home row's clock (what the owner's list shows)."""
    zone = zones.tz(home_zone_name(event))
    return range_text(event.starts_at.astimezone(zone), event.ends_at.astimezone(zone), formats)


def duration_text(event: Event) -> str:
    """`1 h 30 min`, `2 days 3 h`."""
    minutes = int((event.ends_at - event.starts_at).total_seconds() // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = [
        f'{days} day{"" if days == 1 else "s"}' if days else '',
        f'{hours} h' if hours else '',
        f'{mins} min' if mins else '',
    ]
    return ' '.join(part for part in parts if part) or '0 min'


def has_ended(event: Event, now: datetime | None = None) -> bool:
    return (now or timezone.now()) >= event.ends_at


def status_text(event: Event, now: datetime | None = None) -> str:
    """`Starts in 3 h 20 min`, `Happening now: ends in 40 min`, `Ended`: what the page says before
    its script takes over (and all a visitor without JavaScript sees)."""
    now = now or timezone.now()
    if now >= event.ends_at:
        return 'Ended'
    if now >= event.starts_at:
        return f'Happening now: ends in {_span(event.ends_at - now)}'
    return f'Starts in {_span(event.starts_at - now)}'


def _span(delta: timedelta) -> str:
    minutes = max(1, int(delta.total_seconds() // 60))
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f'{days} day{"" if days == 1 else "s"}' + (f' {hours} h' if hours else '')
    if hours:
        return f'{hours} h' + (f' {mins} min' if mins else '')
    return f'{mins} min'


MISSING_TIME_MESSAGE = (
    "That time doesn't exist on that day: the clocks go forward over it. Choose another."
)


def _instant(zone, day: date, moment: time, field: str) -> datetime:
    """`day` at `moment` on `zone`'s clock, as an instant in UTC. A wall time the clocks skip that
    day is refused; one that happens twice (the clocks go back) is its first occurrence."""
    local = datetime.combine(day, moment, tzinfo=zone)
    instant = local.astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise _error(field, MISSING_TIME_MESSAGE)
    return instant


def local_range(zone_name: str, day: date, start: time, end: time) -> tuple[datetime, datetime]:
    """The two instants (UTC) of `day` from `start` to `end` on `zone_name`'s clock; an end that
    is not after the start is the next day's. Compared as instants, never as two wall clocks of one
    zone (see `clean_times`)."""
    zone = zones.tz(zone_name)
    starts_at = _instant(zone, day, start, 'starts_at')
    ends_at = _instant(zone, day, end, 'ends_at')
    if ends_at <= starts_at:
        ends_at = _instant(zone, day + timedelta(days=1), end, 'ends_at')
    return starts_at, ends_at
