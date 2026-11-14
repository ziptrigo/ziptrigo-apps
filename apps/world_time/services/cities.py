"""The city tables: searching them, and seeding them from the GeoNames extract in the repository.

**Why a table and not the file in memory.** The extract is about 32,000 cities with 39,000 other
names. Held in Python that is tens of megabytes *per process* (three web workers and two queue
workers each load their own copy), for a feature few requests use; in the database it costs disk
only. The search (`search_cities`) is one indexed prefix lookup over the other names and one scan
of the 32,000 short city rows (it also matches a later word of a name, which no btree index
serves): a few milliseconds, and bounded by `WT_SEARCH_USER`. If it ever matters, store one row per
word so both halves are index lookups.

**The file** (`apps/world_time/data/cities.tsv.gz`, built by `./manage.py
world_time_build_cities`): gzip, UTF-8, one city per line, tab-separated: GeoNames id, name,
country code, country, region, time zone, population, other names joined by `|`.

**Seeding** (`seed`, run by `./manage.py world_time_seed_cities`, which the container's entrypoint
calls after `migrate`) is idempotent: it compares the file's SHA-256 with the one stored in
`CityData` and does nothing when they match; otherwise, in one transaction, it upserts every city,
deletes the ones no longer in the file and replaces the other names. Users' locations are untouched
either way: they hold their own copy of what they show.
"""

import gzip
import hashlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import available_timezones

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.markdown import normalize_for_search

from ..limits import SEARCH_QUERY_MAX
from ..models import City, CityData, CityName

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parent.parent / 'data' / 'cities.tsv.gz'
_BATCH = 2000
_DELETE_BATCH = 500
#: A search needs this many characters once normalised (one letter would match thousands).
MIN_QUERY_CHARS = 2


def normalize(text: str) -> str:
    """A name as the search compares it: no accents, lower case, punctuation as spaces
    (`São Paulo` and `sao-paulo` are both `sao paulo`)."""
    return normalize_for_search(text)


@dataclass(frozen=True, slots=True)
class CityRow:
    """One line of the city file."""

    geoname_id: int
    name: str
    country_code: str
    country: str
    region: str
    timezone: str
    population: int
    other_names: tuple[str, ...]


def read_file(path: Path = DATA_FILE) -> Iterator[CityRow]:
    """The cities of a city file, in file order. Raises `ValueError` on a malformed line."""
    with gzip.open(path, 'rt', encoding='utf-8', newline='\n') as handle:
        for number, line in enumerate(handle, start=1):
            fields = line.rstrip('\n').split('\t')
            if len(fields) != 8:
                raise ValueError(f'{path.name} line {number}: expected 8 fields, got {len(fields)}')
            yield CityRow(
                geoname_id=int(fields[0]),
                name=fields[1],
                country_code=fields[2],
                country=fields[3],
                region=fields[4],
                timezone=fields[5],
                population=int(fields[6]),
                other_names=tuple(name for name in fields[7].split('|') if name),
            )


def file_sha256(path: Path = DATA_FILE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class SeedResult:
    changed: bool
    """False when the tables already held this file."""
    cities: int
    names: int


def seed(path: Path = DATA_FILE, *, force: bool = False) -> SeedResult:
    """Make the city tables hold `path`'s cities. Idempotent, and a no-op (one query) when the
    tables were last seeded from the same file, unless `force`."""
    digest = file_sha256(path)
    state = CityData.objects.filter(pk=1).first()
    if state is not None and state.sha256 == digest and not force:
        return SeedResult(False, state.city_count, state.name_count)

    rows = list(read_file(path))
    # The row exists before it is locked, so two containers starting at once take turns even on the
    # very first seed (an empty hash means "never seeded").
    CityData.objects.get_or_create(pk=1, defaults={'sha256': '', 'seeded_at': timezone.now()})
    with transaction.atomic():
        state = CityData.objects.select_for_update().get(pk=1)
        if state.sha256 == digest and not force:
            return SeedResult(False, state.city_count, state.name_count)

        # A zone this runtime's tz database lacks would silently show as UTC on a user's row:
        # leave such a city out, and say so.
        known_zones = available_timezones()
        skipped = [row for row in rows if row.timezone not in known_zones]
        if skipped:
            logger.warning(
                'Skipped %d cities whose time zone this system does not know (e.g. %s, %s)',
                len(skipped),
                skipped[0].name,
                skipped[0].timezone,
            )
            rows = [row for row in rows if row.timezone in known_zones]

        # The other names first, so deleting a stale city has nothing to cascade to.
        CityName.objects.all().delete()
        cities = [
            City(
                geoname_id=row.geoname_id,
                name=row.name,
                search_name=normalize(row.name),
                country_code=row.country_code,
                country=row.country,
                region=row.region,
                timezone=row.timezone,
                population=row.population,
            )
            for row in rows
        ]
        City.objects.bulk_create(
            cities,
            batch_size=_BATCH,
            update_conflicts=True,
            unique_fields=['geoname_id'],
            update_fields=[
                'name',
                'search_name',
                'country_code',
                'country',
                'region',
                'timezone',
                'population',
            ],
        )
        # Cities no longer in the file go. Found in Python and deleted in small batches: one
        # `NOT IN` of 32,000 ids is more parameters than SQLite takes.
        stale = sorted(
            set(City.objects.values_list('pk', flat=True)) - {row.geoname_id for row in rows}
        )
        for index in range(0, len(stale), _DELETE_BATCH):
            City.objects.filter(pk__in=stale[index : index + _DELETE_BATCH]).delete()

        names = [
            CityName(city_id=row.geoname_id, name=name, search_name=normalize(name))
            for row in rows
            for name in row.other_names
        ]
        CityName.objects.bulk_create(names, batch_size=_BATCH)

        state.sha256 = digest
        state.city_count = len(cities)
        state.name_count = len(names)
        state.seeded_at = timezone.now()
        state.save()
    logger.info('Seeded %d cities and %d other names from %s', len(cities), len(names), path.name)
    return SeedResult(True, len(cities), len(names))


def is_seeded() -> bool:
    """Whether the city tables have ever been seeded (the search says so when they haven't)."""
    return CityData.objects.filter(pk=1).exclude(sha256='').exists()


@dataclass(frozen=True, slots=True)
class CityMatch:
    """A city the search offers. `matched_name` is the other name the query matched (`Lisboa`),
    empty when it matched the city's own."""

    city: City
    matched_name: str = ''


def search_cities(query: str, limit: int) -> list[CityMatch]:
    """Cities matching `query`: those whose name (or a later word of it) starts with it, and those
    known by another name that starts with it, **together**: a city whose own name is the query
    first, then one known by exactly that name, then the rest, the most populous first in each.

    Every set is read before the cut, so a big city typed by its local name (`Roma`) is not hidden
    behind smaller ones that merely start with it; and the cities named exactly so are read on
    their own (an equality on the indexed column), so a small one whose whole name is a common
    prefix (`Pau`, `Man`, `Bo`) is found however many bigger cities start with it. Three queries."""
    needle = normalize(query[:SEARCH_QUERY_MAX])
    if len(needle) < MIN_QUERY_CHARS:
        return []
    typed = ' '.join(query.split()).casefold()
    exact = City.objects.filter(search_name=needle).order_by('-population', 'geoname_id')[:limit]
    # 0: the city's own name is the query; 1: another of its names is; 2: it only starts with it.
    rank: dict[int, int] = {}
    found: dict[int, CityMatch] = {}
    for city in exact:
        found[city.pk] = CityMatch(city)
        rank[city.pk] = 0
    own = City.objects.filter(
        Q(search_name__startswith=needle) | Q(search_name__contains=f' {needle}')
    ).order_by('-population', 'geoname_id')[:limit]
    for city in own:
        found.setdefault(city.pk, CityMatch(city))
    # More rows than the limit: one city can match by several of its names.
    others = (
        CityName.objects.filter(search_name__startswith=needle)
        .select_related('city')
        .order_by('-city__population', 'city_id', 'name')[: limit * 8]
    )
    by_other = 0
    for other in others:
        if other.search_name == needle:
            rank.setdefault(other.city_id, 1)
        known = found.get(other.city_id)
        if known is not None:
            # Of a city's spellings that match (`Roma`, `Róma`), show the one that was typed.
            if known.matched_name and other.name.casefold() == typed:
                found[other.city_id] = CityMatch(other.city, other.name)
            continue
        if by_other >= limit:
            continue
        by_other += 1
        found[other.city_id] = CityMatch(other.city, other.name)
    matches = sorted(
        found.values(),
        key=lambda match: (rank.get(match.city.pk, 2), -match.city.population, match.city.pk),
    )
    return matches[:limit]
