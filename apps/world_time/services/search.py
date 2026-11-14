"""The location search: cities from the table, then time zones (by name or abbreviation)."""

from dataclasses import dataclass

from ..limits import SEARCH_CITY_RESULTS, SEARCH_QUERY_MAX, SEARCH_ZONE_RESULTS
from . import cities, zones


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One suggestion. `kind` and `value` are what the add form posts (`city=<GeoNames id>` or
    `zone=<IANA name>`)."""

    kind: str
    value: str
    name: str
    detail: str
    hint: str = ''
    """An extra word of why it matched: the other name typed (`Lisboa`)."""


def search(query: str) -> list[SearchResult]:
    """Suggestions for `query`. An abbreviation's zones come first (`PST` means a time zone, not a
    city that starts with those letters), then cities, then zones matched by name."""
    text = ' '.join(str(query or '').split())[:SEARCH_QUERY_MAX]
    if not text:
        return []
    zone_matches = zones.search_zones(text, SEARCH_ZONE_RESULTS)
    as_results = [
        SearchResult('zone', match.zone, match.name, match.detail, 'Time zone')
        for match in zone_matches
    ]
    by_abbreviation = [
        result for result, match in zip(as_results, zone_matches, strict=True) if match.hint
    ]
    by_name = [
        result for result, match in zip(as_results, zone_matches, strict=True) if not match.hint
    ]
    city_results = [
        SearchResult(
            'city',
            str(match.city.pk),
            match.city.name,
            match.city.detail,
            f'also {match.matched_name}' if match.matched_name else '',
        )
        for match in cities.search_cities(text, SEARCH_CITY_RESULTS)
    ]
    return by_abbreviation + city_results + by_name
