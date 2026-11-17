"""Which tasks an export covers (issue #326): the ones **created** in a date range, the "Task
created" select of the account page's To-do block. The same ranges as Receipts' "Receipt date"
(`apps.core.date_ranges`), read in the user's time zone: a day is the user's own, from its local
midnight to the next, so a task created at 23:30 on 31 October where the user lives is October's,
whatever the server's clock says.

`ExportRange` is plain data (a preset or a custom pair, "any date" meaning every task);
`bounds(user)` turns it into the two instants a query needs, `params` into the query-string fields
the block and the dialog pass around, and `describe()` into the words the dialog and the export's
page show.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from apps.accounts.models import User
from apps.accounts.services.timezones import local_datetime, user_today
from apps.core.date_ranges import (
    DATE_RANGE_ANY,
    DATE_RANGE_CUSTOM,
    resolve_date_range,
)
from apps.core.datetime_format import PUBLIC, format_date


def _midnight(user: User, day: date | None) -> datetime | None:
    """The instant `day` starts where `user` lives; `None` for no day, or for one so near the
    ends of the calendar (year 1, year 9999) that its instant doesn't exist in UTC: there is no
    task before or after it either, so it is no bound at all."""
    if day is None:
        return None
    try:
        return local_datetime(user, day, None)
    except OverflowError, ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ExportRange:
    """`date_range` is `any`, `custom` or a preset (`this_month`, ...). The preset is resolved
    against the user's today when the range is used (`resolved`), so one that is kept (an
    export's `filters`) keeps the instants it meant, not the name."""

    date_range: str = DATE_RANGE_ANY
    date_from: date | None = None
    date_to: date | None = None

    def resolved(self, user: User) -> tuple[date | None, date | None]:
        """The inclusive first and last day (either may be `None`: no bound)."""
        if self.date_range == DATE_RANGE_ANY:
            return None, None
        if self.date_range == DATE_RANGE_CUSTOM:
            return self.date_from, self.date_to
        return resolve_date_range(self.date_range, user_today(user))

    def bounds(self, user: User) -> tuple[datetime | None, datetime | None]:
        """`(start, end)` in UTC: tasks created at or after `start` and before `end` (the local
        midnight after the last day), either `None` for no bound."""
        first, last = self.resolved(user)
        start = _midnight(user, first)
        end = _midnight(user, last + timedelta(days=1)) if last and last < date.max else None
        return start, end

    @property
    def params(self) -> dict[str, str]:
        """The query fields that reproduce it (what the block, the dialog and the export keep):
        a preset is just its name, a custom range just its dates, "any date" nothing."""
        if self.date_range == DATE_RANGE_ANY:
            return {}
        if self.date_range == DATE_RANGE_CUSTOM:
            params: dict[str, str] = {}
            if self.date_from:
                params['date_from'] = self.date_from.isoformat()
            if self.date_to:
                params['date_to'] = self.date_to.isoformat()
            return params
        return {'date_range': self.date_range}

    def describe(self, user: User) -> list[str]:
        """What was selected, as short lines ("Task created: ..."); empty for every task."""
        first, last = self.resolved(user)
        if first and last:
            return [
                f'Task created: {format_date(first, "full", PUBLIC)} to '
                f'{format_date(last, "full", PUBLIC)}'
            ]
        if first:
            return [f'Task created: from {format_date(first, "full", PUBLIC)}']
        if last:
            return [f'Task created: until {format_date(last, "full", PUBLIC)}']
        return []
