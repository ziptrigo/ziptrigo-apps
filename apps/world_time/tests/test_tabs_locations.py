"""The services' rules: tabs, and the locations of a tab."""

import pytest
from django.core.exceptions import ValidationError

from ..models import Location, Tab
from ..services import (
    add_city,
    add_zone,
    create_tab,
    delete_tab,
    list_locations,
    list_tabs,
    remove_location,
    rename_tab,
    reorder_locations,
    set_home,
    set_label,
)
from .factories import make_city, make_location, make_tab, make_user

pytestmark = pytest.mark.django_db


def message(excinfo) -> str:
    return '; '.join(excinfo.value.messages)


class TestTabs:
    def test_a_first_visit_gets_a_tab_with_the_account_zone_as_home(self, user):
        (tab,) = list_tabs(user)
        assert tab.name == 'My times'
        (home,) = list_locations(tab)
        assert (home.name, home.timezone, home.is_home, home.geoname_id) == (
            'Lisbon',
            'Europe/Lisbon',
            True,
            None,
        )
        assert [t.pk for t in list_tabs(user)] == [tab.pk], 'a second call makes nothing'

    def test_without_a_zone_the_home_row_is_utc(self):
        (tab,) = list_tabs(make_user(timezone=''))
        assert list_locations(tab)[0].timezone == 'UTC'

    def test_a_legacy_zone_falls_back_to_utc(self):
        (tab,) = list_tabs(make_user(timezone='US/Pacific'))
        assert list_locations(tab)[0].timezone == 'UTC'

    def test_create(self, user):
        first = list_tabs(user)[0]
        tab = create_tab(user, '  Team   Europe ')
        assert tab.name == 'Team Europe' and tab.position == first.position + 1
        assert [row.is_home for row in list_locations(tab)] == [True]

    @pytest.mark.parametrize(
        ('name', 'expected'),
        [('', 'Give the tab a name.'), ('x' * 51, 'Keep the name to 50 characters or fewer.')],
    )
    def test_a_bad_name_is_refused(self, user, name, expected):
        with pytest.raises(ValidationError) as excinfo:
            create_tab(user, name)
        assert expected in message(excinfo)

    def test_names_are_unique_per_user_ignoring_case(self, user, other_user):
        create_tab(user, 'Team')
        with pytest.raises(ValidationError) as excinfo:
            create_tab(user, 'team')
        assert 'You already have a tab with this name.' in message(excinfo)
        create_tab(other_user, 'Team')

    def test_the_cap(self, user, config):
        for name in ('One', 'Two', 'Three'):
            create_tab(user, name)
        with pytest.raises(ValidationError) as excinfo:
            create_tab(user, 'Four')
        assert 'You have too many tabs. Delete one first.' in message(excinfo)

    def test_rename(self, user):
        tab, other = create_tab(user, 'Team'), create_tab(user, 'Family')
        assert rename_tab(tab, 'Work').name == 'Work'
        rename_tab(tab, 'WORK')  # its own name in another case is fine
        with pytest.raises(ValidationError) as excinfo:
            rename_tab(other, 'work')
        assert 'You already have a tab with this name.' in message(excinfo)

    def test_delete_takes_the_locations_but_never_the_last_tab(self, user):
        tab, other = create_tab(user, 'Team'), create_tab(user, 'Family')
        delete_tab(tab)
        assert not Location.objects.filter(tab_id=tab.pk).exists()
        with pytest.raises(ValidationError) as excinfo:
            delete_tab(other)
        assert 'Keep at least one tab.' in message(excinfo)
        assert Tab.objects.filter(pk=other.pk).exists()


class TestLocations:
    @pytest.fixture
    def tab(self, user):
        tab = make_tab(user)
        make_location(tab, 'Lisbon', 'Europe/Lisbon')
        return tab

    def test_add_a_city_copies_what_it_shows(self, tab):
        make_city(
            5,
            'Austin',
            'America/Chicago',
            region='Texas',
            country='United States',
            country_code='US',
        )
        location = add_city(tab, 5)
        assert (location.name, location.region, location.country, location.timezone) == (
            'Austin',
            'Texas',
            'United States',
            'America/Chicago',
        )
        assert (location.geoname_id, location.is_home, location.position) == (5, False, 1)
        assert location.detail == 'Texas, United States' and location.title == 'Austin'

    def test_an_unknown_city_is_refused(self, tab):
        with pytest.raises(ValidationError) as excinfo:
            add_city(tab, 999)
        assert 'Choose a place from the suggestions.' in message(excinfo)

    def test_add_a_zone(self, tab):
        location = add_zone(tab, 'America/Argentina/Buenos_Aires')
        assert (location.name, location.geoname_id) == ('Buenos Aires', None)
        assert location.detail == 'America/Argentina/Buenos Aires'
        assert add_zone(tab, 'UTC').detail == ''

    @pytest.mark.parametrize('zone', ['Mars/Olympus', '', 'US/Pacific'])
    def test_an_unknown_zone_is_refused(self, tab, zone):
        with pytest.raises(ValidationError) as excinfo:
            add_zone(tab, zone)
        assert 'Unknown time zone.' in message(excinfo)

    def test_the_same_place_twice_is_refused(self, tab):
        make_city(5, 'Austin', 'America/Chicago')
        add_city(tab, 5)
        with pytest.raises(ValidationError) as excinfo:
            add_city(tab, 5)
        assert 'This location is already in the tab.' in message(excinfo)
        with pytest.raises(ValidationError):
            add_zone(tab, 'Europe/Lisbon')  # the fixture's row is that bare zone
        # A city and a bare zone of the same zone are different places.
        add_zone(tab, 'America/Chicago')

    def test_the_cap(self, tab, config):
        for zone in ('UTC', 'Asia/Tokyo', 'Asia/Kolkata'):
            add_zone(tab, zone)
        with pytest.raises(ValidationError) as excinfo:
            add_zone(tab, 'Europe/Paris')
        assert 'This tab is full. Remove a location first.' in message(excinfo)

    def test_the_first_location_of_an_empty_tab_is_home(self, user):
        tab = make_tab(user, 'Empty')
        assert add_zone(tab, 'UTC').is_home is True
        assert add_zone(tab, 'Asia/Tokyo').is_home is False

    def test_label(self, tab):
        location = list_locations(tab)[0]
        set_label(location, '  Head   office ')
        location.refresh_from_db()
        assert (location.label, location.title, location.name) == (
            'Head office',
            'Head office',
            'Lisbon',
        )
        set_label(location, '')
        location.refresh_from_db()
        assert location.title == 'Lisbon'
        with pytest.raises(ValidationError) as excinfo:
            set_label(location, 'x' * 61)
        assert 'Keep the label to 60 characters or fewer.' in message(excinfo)

    def test_set_home_moves_the_home(self, tab):
        tokyo = add_zone(tab, 'Asia/Tokyo')
        set_home(tokyo)
        assert [(row.name, row.is_home) for row in list_locations(tab)] == [
            ('Lisbon', False),
            ('Tokyo', True),
        ]
        set_home(tokyo)  # already home: nothing changes
        assert Location.objects.filter(tab=tab, is_home=True).count() == 1

    def test_removing_the_home_makes_the_first_remaining_row_home(self, tab):
        tokyo = add_zone(tab, 'Asia/Tokyo')
        add_zone(tab, 'UTC')
        remove_location(list_locations(tab)[0])
        assert [(row.name, row.is_home) for row in list_locations(tab)] == [
            ('Tokyo', True),
            ('UTC', False),
        ]
        remove_location(tokyo)
        remove_location(tokyo)  # already gone: no error
        assert [row.is_home for row in list_locations(tab)] == [True]

    def test_reorder_ignores_other_tabs_rows(self, tab, user):
        tokyo, utc = add_zone(tab, 'Asia/Tokyo'), add_zone(tab, 'UTC')
        lisbon = list_locations(tab)[0]
        stranger = make_location(make_tab(user, 'Other'), 'Paris', 'Europe/Paris', position=7)
        assert reorder_locations(tab, [utc.pk, stranger.pk, lisbon.pk, tokyo.pk]) == 3
        assert [row.name for row in list_locations(tab)] == ['UTC', 'Lisbon', 'Tokyo']
        stranger.refresh_from_db()
        assert stranger.position == 7

    def test_a_removed_row_cannot_be_changed(self, tab):
        tokyo = add_zone(tab, 'Asia/Tokyo')
        Location.objects.filter(pk=tokyo.pk).delete()
        for change in (lambda: set_label(tokyo, 'x'), lambda: set_home(tokyo)):
            with pytest.raises(ValidationError) as excinfo:
                change()
            assert 'This location no longer exists.' in message(excinfo)
