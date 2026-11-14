"""The locations of a tab: adding a city or a time zone, the label, the home row, order, removal.

Every write runs in one transaction that takes the user's account lock first
(`accounts.lock_account`), so the cap and the "exactly one home" rule hold under concurrent
requests. A refusal is a `ValidationError` keyed by field (`__all__` for none).
"""

from collections.abc import Sequence
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max

from ..limits import LOCATION_LABEL_MAX
from ..models import City, Location, Tab, WorldTimeSettings
from . import zones
from .accounts import get_account, lock_account

DUPLICATE_MESSAGE = 'This location is already in the tab.'
TOO_MANY_MESSAGE = 'This tab is full. Remove a location first.'
UNKNOWN_CITY_MESSAGE = 'Choose a place from the suggestions.'
UNKNOWN_ZONE_MESSAGE = 'Unknown time zone.'
GONE_MESSAGE = 'This location no longer exists.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def clean_label(label: object) -> str:
    cleaned = ' '.join(str(label or '').split())
    if len(cleaned) > LOCATION_LABEL_MAX:
        raise _error('label', f'Keep the label to {LOCATION_LABEL_MAX} characters or fewer.')
    return cleaned


def list_locations(tab: Tab) -> list[Location]:
    """The tab's locations in the user's order."""
    return list(Location.objects.filter(tab=tab).order_by('position', 'created_at', 'pk'))


def _add(tab: Tab, location: Location, duplicate) -> Location:
    get_account(tab.user)
    with transaction.atomic():
        lock_account(tab.user_id)
        if not Tab.objects.filter(pk=tab.pk).exists():
            raise _error('__all__', 'This tab no longer exists.')
        existing = Location.objects.filter(tab=tab)
        if existing.filter(**duplicate).exists():
            raise _error('__all__', DUPLICATE_MESSAGE)
        if existing.count() >= WorldTimeSettings.load().max_locations_per_tab:
            raise _error('__all__', TOO_MANY_MESSAGE)
        top = existing.aggregate(top=Max('position'))['top']
        location.position = 0 if top is None else top + 1
        # The first location of an empty tab is its home.
        location.is_home = not existing.filter(is_home=True).exists()
        try:
            with transaction.atomic():
                location.save()
        except IntegrityError:
            raise _error('__all__', DUPLICATE_MESSAGE) from None
    return location


def add_city(tab: Tab, geoname_id: int) -> Location:
    """Add the city `geoname_id` (from the city table) to the end of the tab."""
    city = City.objects.filter(pk=geoname_id).first()
    if city is None:
        raise _error('city', UNKNOWN_CITY_MESSAGE)
    location = Location(
        tab=tab,
        geoname_id=city.pk,
        name=city.name,
        region=city.region,
        country=city.country,
        country_code=city.country_code,
        timezone=city.timezone,
    )
    return _add(tab, location, {'geoname_id': city.pk})


def add_zone(tab: Tab, zone: str) -> Location:
    """Add a bare time zone (`UTC`, `Europe/Lisbon`) to the end of the tab."""
    zone = str(zone or '').strip()
    if not zones.is_addable_zone(zone):
        raise _error('zone', UNKNOWN_ZONE_MESSAGE)
    location = Location(tab=tab, name=zones.place_name(zone), timezone=zone)
    return _add(tab, location, {'geoname_id__isnull': True, 'timezone': zone})


def _locked(location: Location) -> Location:
    """`location` re-read under the account lock (call inside a transaction)."""
    tab = location.tab
    lock_account(tab.user_id)
    fresh = Location.objects.select_related('tab').filter(pk=location.pk).first()
    if fresh is None:
        raise _error('__all__', GONE_MESSAGE)
    return fresh


def set_label(location: Location, label: str) -> Location:
    """Set (or, with an empty one, clear) the user's own label for the row."""
    cleaned = clean_label(label)
    get_account(location.tab.user)
    with transaction.atomic():
        fresh = _locked(location)
        fresh.label = cleaned
        fresh.save(update_fields=['label'])
    location.label = cleaned
    return location


def set_home(location: Location) -> Location:
    """Make this row the tab's home (the previous home becomes an ordinary row)."""
    get_account(location.tab.user)
    with transaction.atomic():
        fresh = _locked(location)
        if not fresh.is_home:
            Location.objects.filter(tab_id=fresh.tab_id, is_home=True).update(is_home=False)
            fresh.is_home = True
            fresh.save(update_fields=['is_home'])
    location.is_home = True
    return location


def remove_location(location: Location) -> None:
    """Remove the row. If it was the home, the first remaining row becomes the home."""
    get_account(location.tab.user)
    with transaction.atomic():
        try:
            fresh = _locked(location)
        except ValidationError:
            return  # already gone
        tab_id, was_home = fresh.tab_id, fresh.is_home
        fresh.delete()
        if was_home:
            heir = (
                Location.objects.filter(tab_id=tab_id)
                .order_by('position', 'created_at', 'pk')
                .first()
            )
            if heir is not None:
                heir.is_home = True
                heir.save(update_fields=['is_home'])


def reorder_locations(tab: Tab, ids: Sequence[UUID]) -> int:
    """Put the given rows in the order given (the tab's own; other ids are ignored). The rows
    named swap the positions they already hold among themselves, so a list posted from a stale
    page (a row added since is missing from it) can't land two rows on one position; if the tab's
    positions are not distinct to begin with, all its rows are renumbered first."""
    get_account(tab.user)
    with transaction.atomic():
        lock_account(tab.user_id)
        rows = list(Location.objects.filter(tab=tab).order_by('position', 'created_at', 'pk'))
        if len({row.position for row in rows}) != len(rows):
            for index, row in enumerate(rows):
                row.position = index
        by_pk = {row.pk: row for row in rows}
        named = [by_pk[pk] for pk in dict.fromkeys(ids) if pk in by_pk]
        for row, position in zip(named, sorted(row.position for row in named), strict=True):
            row.position = position
        Location.objects.bulk_update(rows, ['position'])
    return len(named)
