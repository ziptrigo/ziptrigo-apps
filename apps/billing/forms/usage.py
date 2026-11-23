"""The usage report's filters, read from the query string (issue #347).

Parsing is lenient, like the dashboards': a value that doesn't validate (a hand-edited URL) is
dropped and the default applies, rather than an error page for a GET. The one refusal the page
shows is the 120-period limit, decided by the view once the dates are known.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlencode

from django.http import QueryDict

from ..services.usage_ranges import CUSTOM, DEFAULT_RANGE, GROUP_KEYS, RANGE_KEYS

_ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_APP_LABEL = re.compile(r'[a-z][a-z0-9_]{0,63}')
MAX_APPS = 20


def parse_date(value: str | None) -> date | None:
    """`YYYY-MM-DD` as a date, else `None` (nothing looser: `20260101` and ISO weeks are not dates here)."""
    text = (value or '').strip()
    if not _ISO_DATE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class UsageParams:
    """The filters of a request: the period, an explicit grouping (`''` = decided from the
    period), and the apps asked for (empty = every app)."""

    range: str = DEFAULT_RANGE
    date_from: date | None = None
    date_to: date | None = None
    group: str = ''
    apps: tuple[str, ...] = field(default_factory=tuple)

    def pairs(self, *, group: bool = True) -> list[tuple[str, str]]:
        """The canonical query parameters, defaults left out (`range` only when it isn't the
        default, the dates only for Custom, `group` only when chosen)."""
        pairs: list[tuple[str, str]] = []
        if self.range != DEFAULT_RANGE:
            pairs.append(('range', self.range))
        if self.range == CUSTOM:
            if self.date_from:
                pairs.append(('date_from', self.date_from.isoformat()))
            if self.date_to:
                pairs.append(('date_to', self.date_to.isoformat()))
        if group and self.group:
            pairs.append(('group', self.group))
        pairs.extend(('app', label) for label in self.apps)
        return pairs

    def to_query(self, *, group: bool = True) -> str:
        return urlencode(self.pairs(group=group))


def parse_params(query: Mapping[str, str] | QueryDict) -> UsageParams:
    """The filters in `query` (a `request.GET`), leniently."""
    get = query.get
    range_key = get('range', '')
    if range_key not in RANGE_KEYS:
        range_key = DEFAULT_RANGE
    group = get('group', '')
    if group not in GROUP_KEYS:
        group = ''
    raw_apps = query.getlist('app') if isinstance(query, QueryDict) else []
    apps: list[str] = []
    for label in raw_apps:
        label = label.strip()
        if _APP_LABEL.fullmatch(label) and label not in apps:
            apps.append(label)
        if len(apps) == MAX_APPS:
            break
    return UsageParams(
        range=range_key,
        date_from=parse_date(get('date_from')) if range_key == CUSTOM else None,
        date_to=parse_date(get('date_to')) if range_key == CUSTOM else None,
        group=group,
        apps=tuple(apps),
    )
