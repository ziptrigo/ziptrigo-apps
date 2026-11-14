"""The grid: which hour it is in every location, on any date, in the user's formats."""

from datetime import UTC, date, datetime

import pytest

from apps.core.datetime_format import PUBLIC, DisplayFormats

from ..services.grid import (
    band_of,
    build_grid,
    dst_note,
    home_today,
    next_change,
    offset_steps,
    offset_text,
    parse_day,
)
from ..services.zones import tz
from .factories import make_location, make_tab

pytestmark = pytest.mark.django_db

MDY_12 = DisplayFormats('mdy', '12h')
DMY_24 = DisplayFormats('dmy', '24h')
NOW = datetime(2026, 11, 19, 15, 30, tzinfo=UTC)


@pytest.fixture
def rows(user):
    tab = make_tab(user)
    return [
        make_location(tab, 'Lisbon', 'Europe/Lisbon'),
        make_location(tab, 'New York', 'America/New_York'),
        make_location(tab, 'Kathmandu', 'Asia/Kathmandu'),
    ]


def test_bands():
    assert [band_of(hour) for hour in (0, 5, 6, 7, 8, 17, 18, 21, 22, 23)] == [
        'night', 'night', 'edge', 'edge', 'day', 'day', 'edge', 'edge', 'night', 'night',
    ]  # fmt: skip


def test_offset_text():
    assert [offset_text(m) for m in (0, 60, -180, 345, -210)] == [
        '+0',
        '+1',
        '-3',
        '+5:45',
        '-3:30',
    ]
    assert offset_text(330, prefix='UTC') == 'UTC+5:30'


def test_parse_day():
    today = date(2026, 11, 19)
    assert parse_day('2026-12-01', today) == date(2026, 12, 1)
    assert parse_day('', today) == today and parse_day(None, today) == today
    assert parse_day('tomorrow', today) == today and parse_day('2026-13-40', today) == today
    assert parse_day('2099-01-01', today) == today, 'more than ten years away'


def test_a_column_is_one_instant_in_every_row(rows):
    grid = build_grid(rows, date(2026, 11, 19), now=NOW, formats=MDY_12)
    assert grid.hours == 24 and grid.start == datetime(2026, 11, 19, 0, 0, tzinfo=UTC)
    lisbon, new_york, kathmandu = grid.rows
    assert [len(row.tiles) for row in grid.rows] == [24, 24, 24]
    assert [len(row.offsets) for row in grid.rows] == [25, 25, 25]
    # 09:00 in Lisbon is 04:00 in New York and 14:45 in Kathmandu.
    assert (lisbon.tiles[9].main, lisbon.tiles[9].sub) == ('9', 'am')
    assert (new_york.tiles[9].main, new_york.tiles[9].sub) == ('4', 'am')
    assert (kathmandu.tiles[9].main, kathmandu.tiles[9].sub) == ('2:45', 'pm')
    assert kathmandu.tiles[9].tight and not lisbon.tiles[9].tight
    assert (lisbon.diff_text, new_york.diff_text, kathmandu.diff_text) == ('', '-5', '+5:45')
    assert (lisbon.utc_text, new_york.utc_text) == ('UTC+0', 'UTC-5')
    assert new_york.offsets[0] == -300 and kathmandu.offsets[0] == 345
    assert new_york.zone_title == 'America/New York · EST · UTC-5'


def test_the_tile_where_a_local_day_starts_shows_the_date(rows):
    grid = build_grid(rows, date(2026, 11, 19), now=NOW, formats=MDY_12)
    lisbon, new_york, _ = grid.rows
    assert (lisbon.tiles[0].main, lisbon.tiles[0].sub, lisbon.tiles[0].day_start) == (
        'Thu',
        '11/19',
        True,
    )
    # New York's midnight is 05:00 in Lisbon; before it, it is still Wednesday evening there.
    assert (new_york.tiles[5].main, new_york.tiles[5].sub) == ('Thu', '11/19')
    assert (new_york.tiles[0].main, new_york.tiles[0].sub) == ('7', 'pm')
    assert new_york.tiles[0].title == 'Wed 11/18/2026, 7:00 PM'


def test_the_account_formats_are_used(rows):
    grid = build_grid(rows, date(2026, 11, 19), now=NOW, formats=DMY_24)
    lisbon, _, kathmandu = grid.rows
    assert (lisbon.tiles[15].main, lisbon.tiles[15].sub) == ('15', '')
    assert lisbon.tiles[0].sub == '19/11'
    assert kathmandu.tiles[9].main == '14:45'
    assert lisbon.tiles[15].title == 'Thu 19/11/2026, 15:00'
    assert lisbon.clock_text == '15:30' and lisbon.date_text == 'Thu 19/11/2026'
    public = build_grid(rows, date(2026, 11, 19), now=NOW, formats=PUBLIC)
    assert public.rows[0].tiles[0].sub == 'Nov 19'


def test_shading_follows_each_rows_own_clock(rows):
    grid = build_grid(rows, date(2026, 11, 21), now=NOW, formats=MDY_12)  # a Saturday
    lisbon, new_york, _ = grid.rows
    assert lisbon.tiles[9].band == 'day' and new_york.tiles[9].band == 'night'
    assert lisbon.tiles[3].weekend is True
    assert new_york.tiles[3].weekend is False, 'still Friday evening in New York'
    assert new_york.tiles[6].weekend is True


def test_the_home_row_decides_the_day(rows):
    rows[0].is_home, rows[1].is_home = False, True
    grid = build_grid(rows, date(2026, 11, 19), now=NOW, formats=MDY_12)
    assert grid.start == datetime(2026, 11, 19, 5, 0, tzinfo=UTC)
    lisbon, new_york, _ = grid.rows
    assert new_york.tiles[0].day_start and new_york.diff_text == ''
    assert lisbon.diff_text == '+5'


@pytest.mark.parametrize(
    ('day', 'hours'),
    [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25), (date(2026, 11, 19), 24)],
)
def test_a_day_the_clocks_change_has_23_or_25_tiles(user, day, hours):
    lisbon = make_location(make_tab(user), 'Lisbon', 'Europe/Lisbon')
    grid = build_grid([lisbon], day, now=NOW, formats=MDY_12)
    assert grid.hours == hours and len(grid.rows[0].tiles) == hours
    assert len(grid.rows[0].offsets) == hours + 1


def test_the_offsets_are_those_of_the_date_shown(user):
    lisbon = make_location(make_tab(user), 'Lisbon', 'Europe/Lisbon')
    summer = build_grid([lisbon], date(2026, 7, 1), now=NOW, formats=MDY_12).rows[0]
    assert summer.offsets[0] == 60 and summer.utc_text == 'UTC+1' and summer.zone_name == 'WEST'
    assert summer.now_offset == 0, 'the live clock uses the offset in force right now'
    autumn = build_grid([lisbon], date(2026, 10, 25), now=NOW, formats=MDY_12).rows[0]
    assert autumn.offsets[:3] == [60, 60, 0]
    assert [tile.main for tile in autumn.tiles[:4]] == ['Sun', '1', '1', '2']


def test_dst_note():
    lisbon = tz('Europe/Lisbon')
    before = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    today = date(2026, 10, 20)
    assert dst_note(lisbon, before, MDY_12, today) == 'Clocks go back 1 hour on Sun 10/25/2026'
    spring = datetime(2026, 3, 25, 12, 0, tzinfo=UTC)
    assert dst_note(lisbon, spring, DMY_24, date(2026, 3, 25)) == (
        'Clocks go forward 1 hour on Sun 29/03/2026'
    )
    assert dst_note(lisbon, datetime(2026, 10, 10, tzinfo=UTC), MDY_12, today) == ''
    assert dst_note(tz('Asia/Tokyo'), before, MDY_12, today) == ''


def test_the_note_is_on_the_rows_whose_clocks_change(rows):
    grid = build_grid(rows, date(2026, 10, 21), now=datetime(2026, 10, 21, 12, tzinfo=UTC))
    lisbon, new_york, kathmandu = grid.rows
    assert lisbon.dst_note.startswith('Clocks go back 1 hour on Sun')
    assert new_york.dst_note == '' and kathmandu.dst_note == ''


def test_today_is_the_home_rows(rows):
    late = datetime(2026, 11, 19, 23, 30, tzinfo=UTC)
    assert home_today(rows, late) == date(2026, 11, 19)
    rows[0].is_home, rows[2].is_home = False, True
    assert home_today(rows, late) == date(2026, 11, 20), 'already tomorrow in Kathmandu'
    grid = build_grid(rows, date(2026, 11, 20), now=late, formats=MDY_12)
    assert grid.is_today and grid.previous_iso == '2026-11-19' and grid.next_iso == '2026-11-21'


def test_an_empty_tab(user):
    grid = build_grid([], date(2026, 11, 19), now=NOW, formats=MDY_12)
    assert grid.rows == [] and grid.payload == {
        'start': 0,
        'hours': 24,
        'minutes': 1440,
        'refreshAt': 0,
        'rows': [],
    }
    assert home_today([], NOW) == date(2026, 11, 19)


def test_the_payload(rows):
    rows[1].label = 'NYC office'
    payload = build_grid(rows, date(2026, 11, 19), now=NOW, formats=MDY_12).payload
    assert payload['start'] == 1795046400000 and payload['hours'] == 24
    assert [(row['title'], row['home'], row['nowOffset']) for row in payload['rows']] == [
        ('Lisbon', True, 0),
        ('NYC office', False, -300),
        ('Kathmandu', False, 345),
    ]
    assert payload['rows'][1]['id'] == str(rows[1].pk)
    assert payload['minutes'] == 1440
    assert [row['steps'] for row in payload['rows']] == [[[0, 0]], [[0, -300]], [[0, 345]]]


def test_a_zone_that_no_longer_loads_is_shown_as_utc(user):
    odd = make_location(make_tab(user), 'Nowhere', 'Mars/Olympus')
    row = build_grid([odd], date(2026, 11, 19), now=NOW, formats=MDY_12).rows[0]
    assert row.utc_text == 'UTC+0'


def test_building_a_grid_makes_no_queries(rows, django_assert_num_queries):
    with django_assert_num_queries(0):
        build_grid(rows, date(2026, 11, 19), now=NOW, formats=MDY_12)


class TestClockChanges:
    """What a review of the first version found: a change inside a tile, at midnight, and a day
    that is not a whole number of hours."""

    def test_a_change_inside_a_tile_is_exact_to_the_minute(self, user):
        # Home is off the hour (India, +5:30), so New York's change at 07:00 UTC falls in the
        # middle of a tile: the tile's first offset would give 2:00 AM, an hour that day lacks.
        tab = make_tab(user)
        rows = [
            make_location(tab, 'Kolkata', 'Asia/Kolkata'),
            make_location(tab, 'New York', 'America/New_York'),
        ]
        grid = build_grid(rows, date(2026, 3, 8), now=NOW, formats=MDY_12)
        assert grid.start == datetime(2026, 3, 7, 18, 30, tzinfo=UTC)
        new_york = grid.rows[1]
        assert new_york.steps == [[0, -300], [750, -240]]
        assert (new_york.offsets[12], new_york.offsets[13]) == (-300, -240)
        assert grid.rows[0].steps == [[0, 330]]

    def test_offset_steps(self):
        start = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
        assert offset_steps(tz('Europe/Lisbon'), start, 1440) == [[0, 60], [60, 0]]
        assert offset_steps(tz('Asia/Tokyo'), start, 1440) == [[0, 540]]
        # Lord Howe Island changes by half an hour.
        lord_howe = offset_steps(
            tz('Australia/Lord_Howe'), datetime(2026, 4, 4, 0, tzinfo=UTC), 1440
        )
        assert lord_howe == [[0, 660], [900, 630]]

    def test_next_change_is_the_exact_instant(self):
        found = next_change(tz('Europe/Lisbon'), datetime(2026, 10, 20, 12, 17, tzinfo=UTC), 7)
        assert found == (datetime(2026, 10, 25, 1, 0, tzinfo=UTC), -60)
        assert next_change(tz('Europe/Lisbon'), datetime(2026, 10, 10, tzinfo=UTC), 7) is None

    @pytest.mark.parametrize(
        ('zone', 'day'),
        [
            ('America/Havana', date(2026, 11, 1)),  # midnight happens twice
            ('America/Havana', date(2026, 3, 8)),  # midnight is skipped: the day starts at 1:00
            ('America/Santiago', date(2026, 9, 6)),
        ],
    )
    def test_a_day_whose_clocks_change_at_midnight_starts_once(self, user, zone, day):
        row = make_location(make_tab(user), 'Somewhere', zone)
        grid = build_grid([row], day, now=NOW, formats=MDY_12)
        starts = [tile for tile in grid.rows[0].tiles if tile.day_start]
        assert len(starts) == 1 and starts[0] is grid.rows[0].tiles[0]
        assert starts[0].main in ('Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat')

    def test_a_day_start_off_the_hour_keeps_its_time(self, rows):
        grid = build_grid(rows, date(2026, 11, 19), now=NOW, formats=MDY_12)
        kathmandu = grid.rows[2]
        (start,) = [tile for tile in kathmandu.tiles if tile.day_start]
        assert (start.main, start.sub) == ('Fri', '12:45')
        assert start.title == 'Fri 11/20/2026, 12:45 AM'
        lisbon_start = grid.rows[0].tiles[0]
        assert (lisbon_start.main, lisbon_start.sub) == ('Thu', '11/19')

    def test_a_day_of_24_and_a_half_hours(self, user):
        row = make_location(make_tab(user), 'Lord Howe', 'Australia/Lord_Howe')
        grid = build_grid([row], date(2026, 4, 5), now=NOW, formats=MDY_12)
        assert (grid.hours, grid.minutes, grid.payload['minutes']) == (25, 1470, 1470)

    def test_when_a_page_left_open_should_load_again(self, rows):
        # Today: at the end of the home row's day.
        today = build_grid(rows, date(2026, 11, 19), now=NOW, formats=MDY_12)
        assert today.refresh_at == datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
        # Another day, no change ahead: never.
        other = build_grid(rows, date(2026, 12, 1), now=NOW, formats=MDY_12)
        assert other.refresh_at is None and other.payload['refreshAt'] == 0
        # A row's clocks change first.
        soon = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
        grid = build_grid(rows, date(2026, 10, 24), now=soon, formats=MDY_12)
        assert grid.is_today and grid.refresh_at == datetime(2026, 10, 24, 23, 0, tzinfo=UTC)
        later = build_grid(rows, date(2026, 10, 30), now=soon, formats=MDY_12)
        assert later.refresh_at == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)

    def test_each_row_says_when_its_day_starts(self, rows):
        grid = build_grid(rows, date(2026, 11, 19), now=NOW, formats=MDY_12)
        assert grid.rows[1].summary == 'The day shown starts at Wed 11/18/2026, 7:00 PM here.'
