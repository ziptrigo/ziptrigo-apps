"""`apps.core.datetime_format` and its template filters (issue #245)."""

import threading
from datetime import UTC, date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.utils import timezone

from apps.core import datetime_format as df
from apps.core.datetime_format import (
    PUBLIC,
    DisplayFormats,
    format_date,
    format_datetime,
    format_time,
    month_day_words,
)

MDY12 = DisplayFormats('mdy', '12h')
DMY24 = DisplayFormats('dmy', '24h')
DAY = date(2026, 11, 5)  # a Thursday
TODAY = date(2026, 6, 15)


def render(source: str, **context) -> str:
    return Template('{% load datetime_format %}' + source).render(Context(context))


# --- dates ---


@pytest.mark.parametrize(
    ('formats', 'style', 'expected'),
    [
        (MDY12, 'full', '11/05/2026'),
        (DMY24, 'full', '05/11/2026'),
        (PUBLIC, 'full', 'Nov 5, 2026'),
        (MDY12, 'compact', '11/05'),
        (DMY24, 'compact', '05/11'),
        (PUBLIC, 'compact', 'Nov 5'),
        (MDY12, 'weekday', 'Thu 11/05/2026'),
        (DMY24, 'weekday', 'Thu 05/11/2026'),
        (PUBLIC, 'weekday', 'Thu Nov 5, 2026'),
        (MDY12, 'heading', 'Thursday, 11/05'),
        (DMY24, 'heading', 'Thursday, 05/11'),
        (PUBLIC, 'heading', 'Thursday, Nov 5'),
    ],
)
def test_date_styles(formats, style, expected):
    assert format_date(DAY, style, formats, today=TODAY) == expected


def test_the_clock_does_not_change_a_date():
    assert format_date(DAY, 'full', DisplayFormats('mdy', '24h')) == '11/05/2026'


def test_day_and_month_are_zero_padded():
    assert format_date(date(2026, 3, 4), 'full', DMY24) == '04/03/2026'
    assert format_date(date(2026, 3, 4), 'full', MDY12) == '03/04/2026'


@pytest.mark.parametrize(
    ('formats', 'expected'),
    [(MDY12, '11/05/2027'), (DMY24, '05/11/2027'), (PUBLIC, 'Nov 5, 2027')],
)
def test_compact_adds_the_year_when_it_is_not_the_current_one(formats, expected):
    assert format_date(date(2027, 11, 5), 'compact', formats, today=TODAY) == expected


def test_heading_adds_the_year_for_another_year():
    assert format_date(date(2027, 11, 5), 'heading', PUBLIC, today=TODAY) == 'Friday, Nov 5, 2027'


def test_compact_compares_with_today_in_the_active_zone(monkeypatch):
    """At New Year the current year is the *user's*, not UTC's."""
    # 2026-12-31 23:30 in UTC is already 2027 in Lisbon's neighbour, Kiritimati (+14).
    moment = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
    monkeypatch.setattr(timezone, 'now', lambda: moment)
    value = date(2027, 1, 2)
    try:
        timezone.activate(ZoneInfo('Pacific/Kiritimati'))
        assert format_date(value, 'compact', MDY12) == '01/02'
        timezone.activate(ZoneInfo('UTC'))
        assert format_date(value, 'compact', MDY12) == '01/02/2027'
    finally:
        timezone.deactivate()


def test_an_aware_datetime_is_converted_to_the_active_zone():
    moment = datetime(2026, 11, 5, 1, 30, tzinfo=UTC)
    try:
        timezone.activate(ZoneInfo('America/Chicago'))
        assert format_date(moment, 'full', MDY12) == '11/04/2026'
        assert format_time(moment, MDY12) == '7:30 PM'
    finally:
        timezone.deactivate()
    assert format_date(moment, 'full', MDY12) == '11/05/2026'


def test_a_naive_datetime_is_taken_as_it_is():
    assert format_date(datetime(2026, 11, 5, 23, 59), 'full', MDY12) == '11/05/2026'


def test_none_and_empty_are_empty():
    assert format_date(None) == ''
    assert format_time(None) == ''
    assert format_datetime(None) == ''
    assert format_date('') == ''  # ty: ignore[invalid-argument-type]


def test_an_unknown_style_is_an_error():
    with pytest.raises(ValueError):
        format_date(DAY, 'long', MDY12)


# --- times ---


@pytest.mark.parametrize(
    ('value', 'twelve', 'twenty_four'),
    [
        (time(0, 0), '12:00 AM', '00:00'),
        (time(0, 5), '12:05 AM', '00:05'),
        (time(9, 5), '9:05 AM', '09:05'),
        (time(12, 0), '12:00 PM', '12:00'),
        (time(12, 30), '12:30 PM', '12:30'),
        (time(13, 7), '1:07 PM', '13:07'),
        (time(21, 5), '9:05 PM', '21:05'),
        (time(23, 59), '11:59 PM', '23:59'),
    ],
)
def test_times(value, twelve, twenty_four):
    assert format_time(value, DisplayFormats('mdy', '12h')) == twelve
    assert format_time(value, DisplayFormats('dmy', '24h')) == twenty_four
    assert format_time(value, PUBLIC) == twelve


def test_a_date_with_a_time():
    moment = datetime(2026, 11, 5, 21, 5)
    assert format_datetime(moment, 'full', MDY12) == '11/05/2026, 9:05 PM'
    assert format_datetime(moment, 'full', DMY24) == '05/11/2026, 21:05'
    assert format_datetime(moment, 'full', PUBLIC) == 'Nov 5, 2026, 9:05 PM'
    assert format_datetime(moment, 'compact', PUBLIC, today=date(2026, 1, 1)) == 'Nov 5, 9:05 PM'


# --- words ---


def test_month_day_words_follow_the_order():
    assert month_day_words(3, 3, MDY12) == 'March 3'
    assert month_day_words(3, 3, PUBLIC) == 'March 3'
    assert month_day_words(3, 3, DMY24) == '3 March'


# --- utc messages ---


def test_utc_messages_use_the_public_wording():
    moment = datetime(
        2026, 11, 5, 21, 5, tzinfo=dt_timezone(timedelta(hours=-5))
    )  # 02:05 UTC next day
    assert df.utc_date_time(moment) == ('Nov 6, 2026', '2:05 AM')
    assert df.format_utc(moment) == 'Nov 6, 2026, 2:05 AM'
    assert df.format_utc(datetime(2026, 11, 5, 21, 5)) == 'Nov 5, 2026, 9:05 PM'


# --- the active formats ---


def test_the_default_is_public():
    assert df.get_active() is PUBLIC


def test_activate_and_deactivate():
    df.activate(DMY24)
    try:
        assert df.get_active() == DMY24
        assert format_date(DAY) == '05/11/2026'
    finally:
        df.deactivate()
    assert df.get_active() is PUBLIC


def test_override_restores_what_was_active():
    df.activate(DMY24)
    try:
        with df.override(PUBLIC):
            assert df.get_active() is PUBLIC
        assert df.get_active() == DMY24
    finally:
        df.deactivate()


def test_override_restores_after_an_error():
    with pytest.raises(RuntimeError):
        with df.override(DMY24):
            raise RuntimeError
    assert df.get_active() is PUBLIC


def test_activation_does_not_leak_between_threads():
    seen = []
    df.activate(DMY24)
    try:
        thread = threading.Thread(target=lambda: seen.append(df.get_active()))
        thread.start()
        thread.join()
    finally:
        df.deactivate()
    assert seen == [PUBLIC]


class _User:
    is_authenticated = True

    def __init__(self, **attributes):
        self.__dict__.update(attributes)


def test_formats_for_a_user():
    assert df.formats_for(_User(date_format='dmy', time_format='24h')) == DMY24
    assert df.formats_for(_User(date_format='mdy', time_format='12h')) == MDY12


def test_formats_for_anonymous_or_missing_is_public():
    class Anonymous:
        is_authenticated = False

    assert df.formats_for(None) is PUBLIC
    assert df.formats_for(Anonymous()) is PUBLIC


def test_formats_for_falls_back_on_missing_or_bad_values():
    assert df.formats_for(_User()) == MDY12
    assert df.formats_for(_User(date_format='x', time_format='y')) == MDY12
    assert df.formats_for(_User(date_format='dmy')) == DisplayFormats('dmy', '12h')


def test_numeric_order_of_public_is_month_first():
    assert PUBLIC.numeric_order == 'mdy'
    assert DMY24.numeric_order == 'dmy'


# --- the filters ---


def test_filters_follow_the_active_formats():
    moment = datetime(2026, 11, 5, 21, 5)
    with df.override(DMY24):
        assert render(
            '{{ d|fdate }}|{{ t|ftime }}|{{ m|fdatetime }}', d=DAY, t=time(21, 5), m=moment
        ) == ('05/11/2026|21:05|05/11/2026, 21:05')
    with df.override(MDY12):
        assert render(
            '{{ d|fdate }}|{{ t|ftime }}|{{ m|fdatetime }}', d=DAY, t=time(21, 5), m=moment
        ) == ('11/05/2026|9:05 PM|11/05/2026, 9:05 PM')
    assert render('{{ d|fdate }}|{{ m|fdatetime }}', d=DAY, m=moment) == (
        'Nov 5, 2026|Nov 5, 2026, 9:05 PM'
    )


def test_filter_styles():
    with df.override(MDY12):
        assert render("{{ d|fdate:'weekday' }}", d=DAY) == 'Thu 11/05/2026'
        assert render("{{ d|fdate:'heading' }}", d=DAY).startswith('Thursday, 11/05')
        assert render("{{ m|fdatetime:'weekday' }}", m=datetime(2026, 11, 5, 9, 5)) == (
            'Thu 11/05/2026, 9:05 AM'
        )


def test_filters_on_none_are_empty():
    assert (
        render('[{{ d|fdate }}][{{ t|ftime }}][{{ m|fdatetime }}]', d=None, t=None, m=None)
        == '[][][]'
    )


def test_an_unknown_style_is_a_template_error():
    with pytest.raises(TemplateSyntaxError):
        render("{{ d|fdate:'nope' }}", d=DAY)
    with pytest.raises(TemplateSyntaxError):
        render("{{ d|fdatetime:'nope' }}", d=datetime(2026, 1, 1))


def test_data_attributes_describe_the_active_formats():
    with df.override(DMY24):
        assert render('{% format_data_attrs %}') == 'data-date-format="dmy" data-time-format="24h"'
    assert render('{% format_data_attrs %}') == 'data-date-format="public" data-time-format="12h"'


def test_sample_tags():
    assert render("{% sample_time '12h' %}|{% sample_time '24h' %}") == '9:05 PM|21:05'
    out = render("{% sample_date 'mdy' %}|{% sample_date 'dmy' %}")
    first, second = out.split('|')
    assert first[:2] == second[3:5] and first[3:5] == second[:2]
    with pytest.raises(TemplateSyntaxError):
        render("{% sample_date 'x' %}")


def test_field_text_tags():
    with df.override(DMY24):
        assert render("{% field_date_text '2026-11-05' %}|{% field_date_placeholder %}") == (
            '05/11/2026|dd/mm/yyyy'
        )
        assert render("{% field_time_text '21:05' %}|{% field_time_placeholder %}") == '21:05|21:00'
    with df.override(MDY12):
        assert render("{% field_date_text '2026-11-05' %}|{% field_date_placeholder %}") == (
            '11/05/2026|mm/dd/yyyy'
        )
        assert render("{% field_time_text '21:05' %}|{% field_time_placeholder %}") == (
            '9:05 PM|9:00 PM'
        )
    # A public page has no account: its fields are typed month first.
    assert render("{% field_date_text '2026-11-05' %}") == '11/05/2026'
    assert render("[{% field_date_text 'bad' %}][{% field_time_text '' %}]") == '[][]'


def test_base_template_carries_the_data_attributes(client):
    assert 'data-date-format="public" data-time-format="12h"' in client.get('/').content.decode()
