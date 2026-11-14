"""A user's tabs: named sets of locations."""

from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max

from apps.accounts.models import User

from ..limits import TAB_NAME_MAX
from ..models import Location, Tab, WorldTimeSettings
from . import zones
from .accounts import get_account, lock_account
from .text import clean_line

DEFAULT_TAB_NAME = 'My times'
DUPLICATE_MESSAGE = 'You already have a tab with this name.'
TOO_MANY_MESSAGE = 'You have too many tabs. Delete one first.'
LAST_TAB_MESSAGE = 'Keep at least one tab.'
GONE_MESSAGE = 'This tab no longer exists.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def clean_name(name: object) -> str:
    cleaned = clean_line(name)
    if not cleaned:
        raise _error('name', 'Give the tab a name.')
    if len(cleaned) > TAB_NAME_MAX:
        raise _error('name', f'Keep the name to {TAB_NAME_MAX} characters or fewer.')
    return cleaned


def home_zone_for(user: User) -> str:
    """The zone a new tab's home row gets: the account's time zone when it is one a location can
    have (an old name such as `US/Pacific` is read as its canonical zone), else UTC."""
    return zones.addable_zone_for(getattr(user, 'timezone', '') or '')


def _home_location(tab: Tab, user: User) -> Location:
    zone = home_zone_for(user)
    return Location(tab=tab, name=zones.place_name(zone), timezone=zone, is_home=True, position=0)


def list_tabs(user: User) -> list[Tab]:
    """The user's tabs in order. A user who has none (a first visit, or an account from before the
    app) gets one, `My times`, whose only row is the account's time zone."""
    tabs = list(Tab.objects.filter(user=user))
    if tabs:
        return tabs
    get_account(user)
    with transaction.atomic():
        lock_account(user.pk)
        tabs = list(Tab.objects.filter(user=user))
        if not tabs:
            tab = Tab.objects.create(user=user, name=DEFAULT_TAB_NAME, position=0)
            _home_location(tab, user).save()
            tabs = [tab]
    return tabs


def get_tab(user: User, tab_id: UUID) -> Tab:
    """The user's tab `tab_id`; `Tab.DoesNotExist` for another user's or an unknown one."""
    return Tab.objects.get(user=user, pk=tab_id)


def create_tab(user: User, name: str) -> Tab:
    """A new tab at the end, with the account's time zone as its home row."""
    cleaned = clean_name(name)
    get_account(user)
    with transaction.atomic():
        lock_account(user.pk)
        existing = Tab.objects.filter(user=user)
        if existing.count() >= WorldTimeSettings.load().max_tabs_per_user:
            raise _error('__all__', TOO_MANY_MESSAGE)
        if existing.filter(name__iexact=cleaned).exists():
            raise _error('name', DUPLICATE_MESSAGE)
        top = existing.aggregate(top=Max('position'))['top']
        try:
            with transaction.atomic():
                tab = Tab.objects.create(
                    user=user, name=cleaned, position=0 if top is None else top + 1
                )
        except IntegrityError:  # a concurrent create of the same name
            raise _error('name', DUPLICATE_MESSAGE) from None
        _home_location(tab, user).save()
    return tab


def rename_tab(tab: Tab, name: str) -> Tab:
    cleaned = clean_name(name)
    get_account(tab.user)
    with transaction.atomic():
        lock_account(tab.user_id)
        if not Tab.objects.filter(pk=tab.pk).exists():
            raise _error('__all__', GONE_MESSAGE)
        if (
            Tab.objects.filter(user_id=tab.user_id, name__iexact=cleaned)
            .exclude(pk=tab.pk)
            .exists()
        ):
            raise _error('name', DUPLICATE_MESSAGE)
        tab.name = cleaned
        try:
            with transaction.atomic():
                tab.save(update_fields=['name'])
        except IntegrityError:
            raise _error('name', DUPLICATE_MESSAGE) from None
    return tab


def delete_tab(tab: Tab) -> None:
    """Delete the tab and its locations. The last tab can't go: there is always one to show."""
    get_account(tab.user)
    with transaction.atomic():
        lock_account(tab.user_id)
        if Tab.objects.filter(user_id=tab.user_id).exclude(pk=tab.pk).count() == 0:
            raise _error('__all__', LAST_TAB_MESSAGE)
        tab.delete()
