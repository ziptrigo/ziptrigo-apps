"""Time zones as locations: the zone names a user can add directly (`UTC`, `Europe/Lisbon`), and
the abbreviations (`PST`, `CET`) the search understands.

An abbreviation is only ever a **search alias** for a real zone: `PST` offers Los Angeles, which is
on PDT half the year, so a fixed-offset "PST" row would be wrong for those months; and several are
ambiguous (`IST` is India, Ireland and Israel), so each offers every zone it can mean.
"""

from dataclasses import dataclass
from functools import cache
from zoneinfo import ZoneInfo

from apps.accounts.services.timezones import timezone_choices
from apps.core.markdown import normalize_for_search

UTC = 'UTC'

#: abbreviation -> the zones it can mean, each with the name people give that time.
ABBREVIATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    'GMT': (('Europe/London', 'Greenwich Mean Time'), (UTC, 'Coordinated Universal Time')),
    'BST': (('Europe/London', 'British Summer Time'),),
    'WET': (('Europe/Lisbon', 'Western European Time'),),
    'WEST': (('Europe/Lisbon', 'Western European Summer Time'),),
    'CET': (('Europe/Berlin', 'Central European Time'),),
    'CEST': (('Europe/Berlin', 'Central European Summer Time'),),
    'EET': (('Europe/Athens', 'Eastern European Time'),),
    'EEST': (('Europe/Athens', 'Eastern European Summer Time'),),
    'MSK': (('Europe/Moscow', 'Moscow Time'),),
    'PT': (('America/Los_Angeles', 'Pacific Time'),),
    'PST': (('America/Los_Angeles', 'Pacific Time'),),
    'PDT': (('America/Los_Angeles', 'Pacific Time'),),
    'MT': (('America/Denver', 'Mountain Time'),),
    'MST': (('America/Denver', 'Mountain Time'), ('America/Phoenix', 'Mountain Standard Time')),
    'MDT': (('America/Denver', 'Mountain Time'),),
    'CT': (('America/Chicago', 'Central Time'),),
    'CST': (('America/Chicago', 'Central Time'), ('Asia/Shanghai', 'China Standard Time')),
    'CDT': (('America/Chicago', 'Central Time'),),
    'ET': (('America/New_York', 'Eastern Time'),),
    'EST': (('America/New_York', 'Eastern Time'),),
    'EDT': (('America/New_York', 'Eastern Time'),),
    'AKST': (('America/Anchorage', 'Alaska Time'),),
    'AKDT': (('America/Anchorage', 'Alaska Time'),),
    'HST': (('Pacific/Honolulu', 'Hawaii Time'),),
    'AST': (('America/Halifax', 'Atlantic Time'), ('Asia/Riyadh', 'Arabia Standard Time')),
    'ADT': (('America/Halifax', 'Atlantic Time'),),
    'NST': (('America/St_Johns', 'Newfoundland Time'),),
    'NDT': (('America/St_Johns', 'Newfoundland Time'),),
    'BRT': (('America/Sao_Paulo', 'Brasilia Time'),),
    'ART': (('America/Argentina/Buenos_Aires', 'Argentina Time'),),
    'IST': (
        ('Asia/Kolkata', 'India Standard Time'),
        ('Europe/Dublin', 'Irish Standard Time'),
        ('Asia/Jerusalem', 'Israel Standard Time'),
    ),
    'PKT': (('Asia/Karachi', 'Pakistan Time'),),
    'GST': (('Asia/Dubai', 'Gulf Standard Time'),),
    'ICT': (('Asia/Bangkok', 'Indochina Time'),),
    'SGT': (('Asia/Singapore', 'Singapore Time'),),
    'HKT': (('Asia/Hong_Kong', 'Hong Kong Time'),),
    'JST': (('Asia/Tokyo', 'Japan Standard Time'),),
    'KST': (('Asia/Seoul', 'Korea Standard Time'),),
    'AWST': (('Australia/Perth', 'Australian Western Time'),),
    'ACST': (('Australia/Adelaide', 'Australian Central Time'),),
    'ACDT': (('Australia/Adelaide', 'Australian Central Time'),),
    'AEST': (
        ('Australia/Sydney', 'Australian Eastern Time'),
        ('Australia/Brisbane', 'Australian Eastern Standard Time'),
    ),
    'AEDT': (('Australia/Sydney', 'Australian Eastern Time'),),
    'NZST': (('Pacific/Auckland', 'New Zealand Time'),),
    'NZDT': (('Pacific/Auckland', 'New Zealand Time'),),
    'SAST': (('Africa/Johannesburg', 'South Africa Standard Time'),),
    'EAT': (('Africa/Nairobi', 'East Africa Time'),),
    'WAT': (('Africa/Lagos', 'West Africa Time'),),
    'CAT': (('Africa/Maputo', 'Central Africa Time'),),
}


@dataclass(frozen=True, slots=True)
class ZoneMatch:
    """A time zone the search offers. `hint` is why (`Pacific Time (PST)`), empty for a match by
    the zone's own name."""

    zone: str
    name: str
    hint: str = ''

    @property
    def detail(self) -> str:
        return self.hint or (self.zone.replace('_', ' ') if self.zone != self.name else '')


def place_name(zone: str) -> str:
    """What a bare zone is called as a location: its city part with spaces (`Buenos Aires` for
    `America/Argentina/Buenos_Aires`), `UTC` for UTC."""
    return zone.rsplit('/', 1)[-1].replace('_', ' ')


@cache
def _zones() -> tuple[tuple[str, str], ...]:
    """`(zone, text the search compares)` for every zone a user can add: the account page's list
    (UTC and the canonical `Area/City` names)."""
    return tuple(
        (name, normalize_for_search(name.replace('/', ' '))) for name, _ in timezone_choices()
    )


def is_addable_zone(zone: str) -> bool:
    """Whether `zone` is one of the zones a user can add as a location."""
    return any(name == zone for name, _ in _zones())


def tz(zone: str) -> ZoneInfo:
    """The `ZoneInfo` of a stored zone name; UTC for one that no longer loads (a zone removed from
    the tz database since the location was saved), so a page never fails over it."""
    try:
        return ZoneInfo(zone)
    except ValueError, OSError, LookupError:
        return ZoneInfo(UTC)


def search_zones(query: str, limit: int) -> list[ZoneMatch]:
    """Zones matching `query`: an abbreviation's zones first (exact, any case), then zones whose
    name has a word starting with it (`lis` finds `Europe/Lisbon`; `europe/lisbon` too)."""
    text = ' '.join(query.split())
    if not text:
        return []
    matches: list[ZoneMatch] = []
    seen: set[str] = set()
    abbreviation = text.upper()
    for zone, meaning in ABBREVIATIONS.get(abbreviation, ()):
        if is_addable_zone(zone) and zone not in seen:
            seen.add(zone)
            matches.append(ZoneMatch(zone, place_name(zone), f'{meaning} ({abbreviation})'))
    needle = normalize_for_search(text.replace('/', ' '))
    if len(needle) >= 2:
        for zone, haystack in _zones():
            if len(matches) >= limit:
                break
            if zone in seen:
                continue
            if haystack.startswith(needle) or f' {needle}' in haystack:
                seen.add(zone)
                matches.append(ZoneMatch(zone, place_name(zone)))
    return matches[:limit]
