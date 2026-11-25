"""The date-range dropdown the account page's export blocks share (Receipts' "Receipt date",
To-do's "Task created"; issue #326 moved it here from Receipts so the two can't drift apart).

A preset is resolved against *today* on every request, so a link to "This month" stays this month;
`custom` uses a `date_from` / `date_to` pair. "All" has a value of its own: picking it fires
the request before Alpine disables the date inputs, so the dates still arrive, and only an explicit
`any` can win over them (no `date_range` at all with dates is an old link, read as Custom).
"""

from calendar import monthrange
from datetime import date, timedelta

DATE_RANGE_ANY = 'any'
DATE_RANGE_CUSTOM = 'custom'
DATE_RANGES = (
    (DATE_RANGE_ANY, 'All'),
    ('last_15_days', 'Last 15 days'),
    ('last_30_days', 'Last 30 days'),
    ('this_month', 'This month'),
    ('this_quarter', 'This quarter'),
    ('this_year', 'This year'),
    (DATE_RANGE_CUSTOM, 'Custom'),
)


def resolve_date_range(key: str, today: date) -> tuple[date, date]:
    """The inclusive `(first, last)` dates of the preset `key`. "Last N days" ends today and
    includes it; the calendar ones cover the whole month, quarter or year."""
    match key:
        case 'last_15_days':
            return today - timedelta(days=14), today
        case 'last_30_days':
            return today - timedelta(days=29), today
        case 'this_month':
            first = today.replace(day=1)
            return first, today.replace(day=monthrange(today.year, today.month)[1])
        case 'this_quarter':
            first_month = (today.month - 1) // 3 * 3 + 1
            last_month = first_month + 2
            return (
                date(today.year, first_month, 1),
                date(today.year, last_month, monthrange(today.year, last_month)[1]),
            )
        case 'this_year':
            return date(today.year, 1, 1), date(today.year, 12, 31)
    raise ValueError(f'Unknown date range: {key}')
