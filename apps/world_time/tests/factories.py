"""Helpers that build users, tabs, locations and cities in the states the tests need."""

import gzip
import uuid
from pathlib import Path

from apps.accounts.models import User

from ..models import City, CityName, Location, Tab
from ..services import cities as city_service
from ..services import get_account


def make_user(**fields) -> User:
    """A confirmed, active user without a password (so no slow password hashing)."""
    fields.setdefault('email', f'user-{uuid.uuid4().hex[:8]}@example.com')
    fields.setdefault('email_confirmed', True)
    return User.objects.create_user(**fields)


def make_tab(user: User, name: str = 'Team', **fields) -> Tab:
    """A tab straight in the database, with no rows (the services give a new tab a home row)."""
    get_account(user)
    fields.setdefault('position', Tab.objects.filter(user=user).count())
    return Tab.objects.create(user=user, name=name, **fields)


def make_location(tab: Tab, name: str = 'Lisbon', timezone: str = 'Europe/Lisbon', **fields):
    """A location straight in the database: the first one of a tab is its home."""
    existing = Location.objects.filter(tab=tab)
    fields.setdefault('position', existing.count())
    fields.setdefault('is_home', not existing.filter(is_home=True).exists())
    return Location.objects.create(tab=tab, name=name, timezone=timezone, **fields)


def make_city(geoname_id: int, name: str, timezone: str = 'Europe/Lisbon', **fields) -> City:
    fields.setdefault('country_code', 'PT')
    fields.setdefault('country', 'Portugal')
    fields.setdefault('population', 1000)
    other_names = fields.pop('other_names', ())
    city = City.objects.create(
        geoname_id=geoname_id,
        name=name,
        search_name=city_service.normalize(name),
        timezone=timezone,
        **fields,
    )
    for other in other_names:
        CityName.objects.create(city=city, name=other, search_name=city_service.normalize(other))
    return city


def write_city_file(path: Path, rows: list[tuple]) -> Path:
    """A city file like the repository's: `(id, name, country code, country, region, zone,
    population, [other names])` per row."""
    lines = [
        '\t'.join(
            [str(row[0]), row[1], row[2], row[3], row[4], row[5], str(row[6]), '|'.join(row[7])]
        )
        for row in rows
    ]
    with gzip.open(path, 'wt', encoding='utf-8', newline='\n') as handle:
        handle.write(''.join(f'{line}\n' for line in lines))
    return path
