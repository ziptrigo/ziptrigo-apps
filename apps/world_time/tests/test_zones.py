"""Time zones as locations: the names, the abbreviations and the zone search."""

from zoneinfo import available_timezones

from ..services import zones
from ..services.zones import ABBREVIATIONS, search_zones


def test_every_abbreviation_points_at_zones_a_user_can_add():
    known = available_timezones()
    for abbreviation, meanings in ABBREVIATIONS.items():
        assert abbreviation.isupper() and meanings
        for zone, meaning in meanings:
            assert zone in known and zones.is_addable_zone(zone), (abbreviation, zone)
            assert meaning


def test_place_name():
    assert zones.place_name('UTC') == 'UTC'
    assert zones.place_name('Europe/Lisbon') == 'Lisbon'
    assert zones.place_name('America/Argentina/Buenos_Aires') == 'Buenos Aires'


def test_addable_zones_are_the_canonical_ones():
    assert zones.is_addable_zone('UTC') and zones.is_addable_zone('Asia/Kolkata')
    assert not zones.is_addable_zone('US/Pacific')
    assert not zones.is_addable_zone('Mars/Olympus') and not zones.is_addable_zone('')


def test_an_abbreviation_is_an_alias_for_real_zones():
    (match,) = search_zones('pst', 4)
    assert (match.zone, match.name, match.hint) == (
        'America/Los_Angeles',
        'Los Angeles',
        'Pacific Time (PST)',
    )


def test_an_ambiguous_abbreviation_offers_every_meaning():
    matches = search_zones('IST', 4)
    assert [match.zone for match in matches[:3]] == [
        'Asia/Kolkata',
        'Europe/Dublin',
        'Asia/Jerusalem',
    ]
    assert matches[0].hint == 'India Standard Time (IST)'
    # Then the zones whose own name starts with it, with no hint.
    assert (matches[3].zone, matches[3].hint) == ('Europe/Istanbul', '')


def test_zone_names_match_by_word_and_in_full():
    assert search_zones('lisb', 4)[0].zone == 'Europe/Lisbon'
    assert search_zones('Europe/Lisbon', 4)[0].zone == 'Europe/Lisbon'
    assert search_zones('buenos', 4)[0].name == 'Buenos Aires'
    assert search_zones('utc', 4)[0].zone == 'UTC'
    assert search_zones('zzzz', 4) == [] and search_zones('', 4) == []
    assert len(search_zones('america', 3)) == 3


def test_a_zone_that_no_longer_loads_reads_as_utc():
    assert zones.tz('Mars/Olympus').key == 'UTC'
    assert zones.tz('Europe/Lisbon').key == 'Europe/Lisbon'


def test_an_accounts_old_zone_name_maps_to_a_zone_a_location_can_have():
    assert zones.addable_zone_for('Europe/Lisbon') == 'Europe/Lisbon'
    assert zones.addable_zone_for('US/Eastern') == 'America/New_York'
    assert zones.addable_zone_for('') == 'UTC' and zones.addable_zone_for('Etc/GMT+5') == 'UTC'
    for old, new in zones.LEGACY_ZONES.items():
        assert not zones.is_addable_zone(old) or old == new, old
        assert zones.is_addable_zone(new), new
