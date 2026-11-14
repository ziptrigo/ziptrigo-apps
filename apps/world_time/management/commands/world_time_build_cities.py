"""`./manage.py world_time_build_cities`: rebuild `apps/world_time/data/cities.tsv.gz` from
GeoNames (https://www.geonames.org, CC BY 4.0). A maintainer's tool, run by hand to refresh the
data; commit the result, and the next deploy seeds it.

It reads three files of the GeoNames dump, downloaded unless `--source` names a folder that has
them: `cities15000.zip` (cities of 15,000 people or more, and capitals), `countryInfo.txt` (country
names) and `admin1CodesASCII.txt` (region names). Stdlib only, no database.

What is kept per city: id, name, country, region, time zone, population, and, for a city of at
least `OTHER_NAMES_MIN_POPULATION` people, its other names in Latin script (`Lisboa`, `München`):
GeoNames lists a city's names in every language and script with no marker in this file, so the
search only takes the ones a Latin keyboard types, and only for the larger cities, which keeps the
table small. Airport codes and names that normalise to the city's own are dropped, and so are
sections of a city (GeoNames feature code `PPLX`).
"""

import gzip
import io
import unicodedata
import urllib.request
import zipfile
from pathlib import Path
from zoneinfo import available_timezones

from django.core.management.base import BaseCommand, CommandError

from apps.core.markdown import normalize_for_search
from apps.world_time.services import cities

BASE_URL = 'https://download.geonames.org/export/dump/'
FILES = ('cities15000.zip', 'countryInfo.txt', 'admin1CodesASCII.txt')
OTHER_NAMES_MIN_POPULATION = 100_000
MAX_OTHER_NAMES = 80
#: A longer "name" is a description, not something typed into a search box.
MAX_NAME_CHARS = 60


def _is_latin(text: str) -> bool:
    return all(ord(char) < 0x250 or unicodedata.category(char).startswith('M') for char in text)


def _other_names(name: str, raw: str) -> list[str]:
    """The other names worth searching by: Latin script, at least 3 characters, no digits, not an
    all-capitals code, starting with a capital, and different from the city's own name once
    normalised. Spellings that
    differ only by accents are all kept (`Roma` and `Róma`, `Munchen` and `München`): the file has
    no language to choose by, and the search shows the one that was typed."""
    own = normalize_for_search(name)
    kept: set[str] = set()
    for other in raw.split(','):
        other = other.strip()
        key = normalize_for_search(other)
        if (
            len(key) < 3
            or len(other) > MAX_NAME_CHARS
            or key == own
            or not _is_latin(other)
            or any(char.isdigit() for char in other)
            or (other.isupper() and len(other) <= 4)
            # A name with no capital is a romanisation of another script (`li si ben`, and
            # `porto` for Bordeaux), not something that names the city in the Latin alphabet.
            or other == other.lower()
            or '|' in other
            or '\t' in other
        ):
            continue
        kept.add(other)
    return sorted(kept, key=lambda other: (normalize_for_search(other), other))[:MAX_OTHER_NAMES]


class Command(BaseCommand):
    help = 'Rebuild the World Time city file from the GeoNames dump (downloads about 4 MB).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            type=Path,
            help='A folder that already has cities15000.zip, countryInfo.txt and '
            'admin1CodesASCII.txt; nothing is downloaded.',
        )
        parser.add_argument('--out', type=Path, default=cities.DATA_FILE)

    def _read(self, source: Path | None, name: str) -> bytes:
        if source is not None:
            return (source / name).read_bytes()
        self.stdout.write(f'Downloading {name} ...')
        with urllib.request.urlopen(BASE_URL + name, timeout=120) as response:  # noqa: S310
            return response.read()

    def handle(self, *args, **options):
        source = options['source']
        countries = {}
        for line in self._read(source, 'countryInfo.txt').decode('utf-8').splitlines():
            if line and not line.startswith('#'):
                fields = line.split('\t')
                countries[fields[0]] = fields[4]
        regions = {}
        for line in self._read(source, 'admin1CodesASCII.txt').decode('utf-8').splitlines():
            fields = line.split('\t')
            if len(fields) >= 2:
                regions[fields[0]] = fields[1]
        with zipfile.ZipFile(io.BytesIO(self._read(source, 'cities15000.zip'))) as archive:
            text = archive.read('cities15000.txt').decode('utf-8')

        known_zones = available_timezones()
        lines = []
        skipped = 0
        for line in text.splitlines():
            fields = line.split('\t')
            if len(fields) < 19:
                raise CommandError(f'Unexpected line in cities15000.txt: {line[:80]!r}')
            geoname_id, name, country_code = int(fields[0]), fields[1], fields[8]
            zone, population = fields[17], int(fields[14] or 0)
            # `PPLX` is a section of a city (Manhattan, a district of Turin): it would answer to
            # the city's own names (`nyc`, `torino`) ahead of the city.
            if zone not in known_zones or not name or '\t' in name or fields[7] == 'PPLX':
                skipped += 1
                continue
            region = regions.get(f'{country_code}.{fields[10]}', '')
            if normalize_for_search(region) == normalize_for_search(name):
                region = ''  # "Singapore, Singapore"
            others = (
                _other_names(name, fields[3]) if population >= OTHER_NAMES_MIN_POPULATION else []
            )
            lines.append(
                (
                    geoname_id,
                    '\t'.join(
                        [
                            str(geoname_id),
                            name,
                            country_code,
                            countries.get(country_code, ''),
                            region,
                            zone,
                            str(population),
                            '|'.join(others),
                        ]
                    ),
                )
            )
        lines.sort()
        body = ''.join(f'{line}\n' for _, line in lines).encode('utf-8')
        out: Path = options['out']
        out.parent.mkdir(parents=True, exist_ok=True)
        # `mtime=0` and no file name in the header: the same input always gives the same bytes, so
        # an unchanged rebuild is no diff and no re-seed.
        with (
            open(out, 'wb') as raw,
            gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=9) as packed,
        ):
            packed.write(body)
        names = sum(1 for _ in cities.read_file(out))
        self.stdout.write(
            self.style.SUCCESS(
                f'Wrote {names:,} cities to {out} ({out.stat().st_size:,} bytes; '
                f'{skipped} skipped: a section of a city, or an unknown time zone).'
            )
        )
