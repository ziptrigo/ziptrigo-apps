"""The periods and groupings of the credit usage report (issue #347).

A period key is resolved against *today* (in the account's time zone) on every request, so a link
to "Last 7 days" stays relative. Dates are inclusive local dates; the caller turns them into
instants with `apps.accounts.services.timezones.local_datetime`. Pure functions, no queries.
"""

from calendar import monthrange
from datetime import date, timedelta

from django.core.exceptions import ValidationError

LAST_7_DAYS = 'last_7_days'
LAST_30_DAYS = 'last_30_days'
LAST_90_DAYS = 'last_90_days'
THIS_MONTH = 'this_month'
LAST_MONTH = 'last_month'
THIS_QUARTER = 'this_quarter'
THIS_YEAR = 'this_year'
LAST_12_MONTHS = 'last_12_months'
ALL_TIME = 'all_time'
CUSTOM = 'custom'

#: The period choices, in the order the select lists them.
RANGES: tuple[tuple[str, str], ...] = (
    (LAST_7_DAYS, 'Last 7 days'),
    (LAST_30_DAYS, 'Last 30 days'),
    (LAST_90_DAYS, 'Last 90 days'),
    (THIS_MONTH, 'This month'),
    (LAST_MONTH, 'Last month'),
    (THIS_QUARTER, 'This quarter'),
    (THIS_YEAR, 'This year'),
    (LAST_12_MONTHS, 'Last 12 months'),
    (ALL_TIME, 'All time'),
    (CUSTOM, 'Custom'),
)
RANGE_KEYS = tuple(key for key, _label in RANGES)
DEFAULT_RANGE = LAST_30_DAYS

DAY = 'day'
WEEK = 'week'
MONTH = 'month'
QUARTER = 'quarter'
YEAR = 'year'

#: The groupings, finest first.
GROUPS: tuple[tuple[str, str], ...] = (
    (DAY, 'Day'),
    (WEEK, 'Week'),
    (MONTH, 'Month'),
    (QUARTER, 'Quarter'),
    (YEAR, 'Year'),
)
GROUP_KEYS = tuple(key for key, _label in GROUPS)

#: More periods than this are refused: a chart of 500 bars says nothing and costs a lot to draw.
MAX_PERIODS = 120

#: The earliest day a report starts on (a hand-made custom date can't ask for the year 1).
MIN_DATE = date(2000, 1, 1)

_LAST_N_DAYS = {LAST_7_DAYS: 7, LAST_30_DAYS: 30, LAST_90_DAYS: 90}


class PeriodLimitError(ValidationError):
    """A grouping that would make more than `MAX_PERIODS` periods."""

    def __init__(self, count: int, group: str) -> None:
        self.count = count
        self.group = group
        super().__init__(
            f'That is {count} {group}s, too many to show. '
            'Choose a shorter period or group by a longer unit.'
        )


def needs_first_transaction(key: str, date_from: date | None) -> bool:
    """Whether `resolve` needs the date of the user's first transaction (an open start)."""
    return key == ALL_TIME or (key == CUSTOM and date_from is None)


def _months_back(today: date, months: int) -> date:
    """`today` minus `months` calendar months (the day cut to a shorter month's last)."""
    index = today.year * 12 + today.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(today.day, monthrange(year, month)[1]))


def resolve(
    key: str,
    today: date,
    date_from: date | None = None,
    date_to: date | None = None,
    first_transaction: date | None = None,
) -> tuple[date, date]:
    """The inclusive `(first, last)` dates of period `key`.

    "Last N days" ends today and includes it; the calendar ones (this month, quarter, year) end
    today too, not at the end of the calendar period, since nothing has been spent later. "All
    time" starts at `first_transaction` (today when there is none). Custom takes the two dates,
    puts them right when swapped, starts at the first transaction and ends today when one is
    missing, and is cut to today at the end. Nothing starts before `MIN_DATE`.
    """
    match key:
        case 'last_7_days' | 'last_30_days' | 'last_90_days':
            first, last = today - timedelta(days=_LAST_N_DAYS[key] - 1), today
        case 'this_month':
            first, last = today.replace(day=1), today
        case 'last_month':
            this_first = today.replace(day=1)
            last = this_first - timedelta(days=1)
            first = last.replace(day=1)
        case 'this_quarter':
            first, last = date(today.year, (today.month - 1) // 3 * 3 + 1, 1), today
        case 'this_year':
            first, last = date(today.year, 1, 1), today
        case 'last_12_months':
            first, last = _months_back(today, 12) + timedelta(days=1), today
        case 'all_time':
            first, last = first_transaction or today, today
        case 'custom':
            first = date_from or first_transaction or today
            last = date_to or today
            if first > last:
                first, last = last, first
        case _:
            raise ValueError(f'Unknown period: {key}')
    last = min(max(last, MIN_DATE), today)
    first = min(max(first, MIN_DATE), last)
    return first, last


def auto_group(first: date, last: date) -> str:
    """The grouping a period of `first` to `last` opens with: days up to 31 days, weeks up to 190,
    months up to 1,100 (about three years), else years."""
    days = (last - first).days + 1
    if days <= 31:
        return DAY
    if days <= 190:
        return WEEK
    if days <= 1100:
        return MONTH
    return YEAR


def period_start(day: date, group: str) -> date:
    """The first day of the period of `group` that `day` is in (weeks start on Monday)."""
    match group:
        case 'day':
            return day
        case 'week':
            return day - timedelta(days=day.weekday())
        case 'month':
            return day.replace(day=1)
        case 'quarter':
            return date(day.year, (day.month - 1) // 3 * 3 + 1, 1)
        case 'year':
            return date(day.year, 1, 1)
    raise ValueError(f'Unknown grouping: {group}')


def next_start(start: date, group: str) -> date:
    """The first day of the period after the one that starts on `start`."""
    match group:
        case 'day':
            return start + timedelta(days=1)
        case 'week':
            return start + timedelta(days=7)
        case 'month':
            return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        case 'quarter':
            index = start.year * 12 + start.month - 1 + 3
            return date(index // 12, index % 12 + 1, 1)
        case 'year':
            return date(start.year + 1, 1, 1)
    raise ValueError(f'Unknown grouping: {group}')


def count_periods(first: date, last: date, group: str) -> int:
    """How many periods of `group` cover `first` to `last` (the first and last may be partial)."""
    match group:
        case 'day':
            return (last - first).days + 1
        case 'week':
            return (period_start(last, 'week') - period_start(first, 'week')).days // 7 + 1
        case 'month':
            return (last.year - first.year) * 12 + last.month - first.month + 1
        case 'quarter':
            return (last.year - first.year) * 4 + (last.month - 1) // 3 - (first.month - 1) // 3 + 1
        case 'year':
            return last.year - first.year + 1
    raise ValueError(f'Unknown grouping: {group}')


def period_starts(first: date, last: date, group: str) -> list[date]:
    """The start of every period of `group` from `first` to `last`, oldest first."""
    starts = []
    current = period_start(first, group)
    while current <= last:
        starts.append(current)
        current = next_start(current, group)
    return starts
