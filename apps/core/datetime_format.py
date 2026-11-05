"""How the site writes dates and times (issue #245).

A signed-in user picks, on the account page, a numeric date order (`mm/dd/yyyy` or `dd/mm/yyyy`)
and a clock (12-hour or 24-hour). `core` can't import `accounts`, so this module deals in plain
values: a `DisplayFormats` and a context variable holding the ones active for the current request.

- `accounts.middleware.TimezoneMiddleware` activates the signed-in user's formats for the request
  and deactivates them again afterwards. A path it skips (the public redirects and download pages,
  static files, the API) and any anonymous visitor therefore render `PUBLIC`: the fixed wording
  every public page uses, `Nov 5, 2026` and `Nov 5, 2026, 9:05 PM`.
- Code that has no request (an email, a push notification, a job) passes `PUBLIC` explicitly or
  runs inside `override(PUBLIC)`; it must never rely on whatever was active last in the thread.

Month names, weekday names and AM/PM are written here by hand: Django's `date` filter and
`strftime` would follow the process locale, and a date that reads differently on two servers is
exactly what this module exists to prevent.

Styles, for a date (`format_date`):

    full      11/05/2026   |  05/11/2026   |  Nov 5, 2026
    compact   11/05        |  05/11        |  Nov 5          (the year is added when it isn't the
                                                              current one in the active time zone)
    weekday   Thu 11/05/2026 | Thu 05/11/2026 | Thu Nov 5, 2026
    heading   Thursday, 11/05 | Thursday, 05/11 | Thursday, Nov 5

and a time (`format_time`): `9:05 PM` or `21:05` (midnight is `12:00 AM`, noon `12:00 PM`). A date
with a time (`format_datetime`) is `date, time`.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from django.utils import timezone

MDY = 'mdy'
DMY = 'dmy'
#: The date "order" of the fixed public wording (month as three letters), not a user choice.
PUBLIC_ORDER = 'public'
CLOCK_12H = '12h'
CLOCK_24H = '24h'

DATE_ORDERS = (MDY, DMY)
CLOCKS = (CLOCK_12H, CLOCK_24H)
STYLES = ('full', 'compact', 'weekday', 'heading')

MONTH_ABBR = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
MONTH_NAMES = (
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
)
WEEKDAY_NAMES = (
    'Monday',
    'Tuesday',
    'Wednesday',
    'Thursday',
    'Friday',
    'Saturday',
    'Sunday',
)


@dataclass(frozen=True)
class DisplayFormats:
    """A date order (`mdy`, `dmy`, or `public` for the three-letter-month wording) and a clock."""

    date_order: str
    clock: str

    @property
    def is_public(self) -> bool:
        return self.date_order == PUBLIC_ORDER

    @property
    def numeric_order(self) -> str:
        """The order a *numeric* date is typed in: the user's, `mdy` for `PUBLIC` (a field on a
        public page, such as the anonymous send form, has no account to take it from)."""
        return MDY if self.is_public else self.date_order


#: The fixed wording of every public page, email and notification.
PUBLIC = DisplayFormats(PUBLIC_ORDER, CLOCK_12H)

_active: ContextVar[DisplayFormats] = ContextVar('display_formats', default=PUBLIC)


def formats_for(user: object) -> DisplayFormats:
    """The formats of `user` (duck-typed: `date_format` and `time_format` attributes); `PUBLIC` for
    an anonymous visitor, `None` or anything without valid values."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return PUBLIC
    order = getattr(user, 'date_format', None)
    clock = getattr(user, 'time_format', None)
    if order not in DATE_ORDERS:
        order = MDY
    if clock not in CLOCKS:
        clock = CLOCK_12H
    return DisplayFormats(order, clock)


def activate(formats: DisplayFormats) -> None:
    _active.set(formats)


def deactivate() -> None:
    _active.set(PUBLIC)


def get_active() -> DisplayFormats:
    return _active.get()


@contextmanager
def override(formats: DisplayFormats) -> Generator[None]:
    """Make `formats` the active ones inside the block, restoring what was active afterwards."""
    token = _active.set(formats)
    try:
        yield
    finally:
        _active.reset(token)


# -- Pieces --------------------------------------------------------------------------------------


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date()
    return value


def _current_year(today: date | None) -> int:
    return (today or timezone.localdate()).year


def _weekday(value: date) -> str:
    return WEEKDAY_NAMES[value.weekday()]


def month_day_words(month: int, day: int, formats: DisplayFormats | None = None) -> str:
    """A month and day in words, in the user's order: `March 3` (`mdy` and `PUBLIC`), `3 March`
    (`dmy`). Used by the repeat sentences, which keep words."""
    formats = formats or get_active()
    name = MONTH_NAMES[month - 1]
    return f'{day} {name}' if formats.date_order == DMY else f'{name} {day}'


def format_date(
    value: date | datetime | None,
    style: str = 'full',
    formats: DisplayFormats | None = None,
    *,
    today: date | None = None,
) -> str:
    """`value` as a date in `style` (see the module docstring); `''` for `None`. `today` is the
    date `compact` compares the year with (default: today in the active time zone). An aware
    datetime is converted to the active time zone first, as `|date` does."""
    if value is None or value == '':
        return ''
    if style not in STYLES:
        raise ValueError(f'Unknown date style {style!r}; use one of {", ".join(STYLES)}')
    formats = formats or get_active()
    day = _as_date(value)
    show_year = style in ('full', 'weekday') or day.year != _current_year(today)
    if formats.is_public:
        month = MONTH_ABBR[day.month - 1]
        text = f'{month} {day.day}'
        if show_year:
            text += f', {day.year}'
        if style == 'weekday':
            return f'{_weekday(day)[:3]} {text}'
        if style == 'heading':
            return f'{_weekday(day)}, {text}'
        return text
    first, second = (day.month, day.day) if formats.date_order == MDY else (day.day, day.month)
    text = f'{first:02d}/{second:02d}'
    if show_year:
        text += f'/{day.year}'
    if style == 'weekday':
        return f'{_weekday(day)[:3]} {text}'
    if style == 'heading':
        return f'{_weekday(day)}, {text}'
    return text


def format_time(
    value: time | datetime | None,
    formats: DisplayFormats | None = None,
) -> str:
    """`value` as a time of day: `9:05 PM` or `21:05`; `''` for `None`. An aware datetime is
    converted to the active time zone first; a `time` is shown as it is."""
    if value is None or value == '':
        return ''
    formats = formats or get_active()
    if isinstance(value, datetime) and timezone.is_aware(value):
        value = timezone.localtime(value)
    hour, minute = value.hour, value.minute
    if formats.clock == CLOCK_24H:
        return f'{hour:02d}:{minute:02d}'
    suffix = 'AM' if hour < 12 else 'PM'
    return f'{hour % 12 or 12}:{minute:02d} {suffix}'


def format_datetime(
    value: datetime | None,
    style: str = 'full',
    formats: DisplayFormats | None = None,
    *,
    today: date | None = None,
) -> str:
    """`value` as `date, time` (`11/05/2026, 9:05 PM`, `Nov 5, 2026, 9:05 PM`)."""
    if value is None or value == '':
        return ''
    formats = formats or get_active()
    return f'{format_date(value, style, formats, today=today)}, {format_time(value, formats)}'


# -- Messages ------------------------------------------------------------------------------------


def _utc_naive(value: datetime) -> datetime:
    """`value` as a naive UTC datetime (a naive one is taken to be UTC already)."""
    if timezone.is_aware(value):
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def utc_date_time(value: datetime) -> tuple[str, str]:
    """`('Nov 5, 2026', '9:05 PM')`: `value` in UTC, in the public wording, for an email, which has
    no account's formats to follow and says "UTC" next to it."""
    naive = _utc_naive(value)
    return format_date(naive, 'full', PUBLIC), format_time(naive, PUBLIC)


def format_utc(value: datetime) -> str:
    """`Nov 5, 2026, 9:05 PM` (UTC, public wording); the caller adds "UTC" where it is not obvious."""
    return ', '.join(utc_date_time(value))
