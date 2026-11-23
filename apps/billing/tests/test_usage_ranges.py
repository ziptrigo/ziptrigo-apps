"""The usage report's periods and groupings (issue #347)."""

from datetime import date

import pytest

from apps.billing.services.usage_ranges import (
    MAX_PERIODS,
    MIN_DATE,
    RANGE_KEYS,
    PeriodLimitError,
    auto_group,
    count_periods,
    needs_first_transaction,
    next_start,
    period_start,
    period_starts,
    resolve,
)

TODAY = date(2026, 11, 23)  # a Monday


@pytest.mark.parametrize(
    ('key', 'first', 'last'),
    [
        ('last_7_days', date(2026, 11, 17), TODAY),
        ('last_30_days', date(2026, 10, 25), TODAY),
        ('last_90_days', date(2026, 8, 26), TODAY),
        ('this_month', date(2026, 11, 1), TODAY),
        ('last_month', date(2026, 10, 1), date(2026, 10, 31)),
        ('this_quarter', date(2026, 10, 1), TODAY),
        ('this_year', date(2026, 1, 1), TODAY),
        ('last_12_months', date(2025, 11, 24), TODAY),
    ],
)
def test_every_preset(key, first, last):
    assert resolve(key, TODAY) == (first, last)


def test_every_key_resolves():
    for key in RANGE_KEYS:
        first, last = resolve(key, TODAY, date(2026, 1, 1), date(2026, 2, 1), date(2025, 9, 14))
        assert first <= last <= TODAY


def test_last_month_in_january_is_december_of_the_year_before():
    assert resolve('last_month', date(2027, 1, 10)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_quarter_edges():
    assert resolve('this_quarter', date(2026, 1, 1)) == (date(2026, 1, 1), date(2026, 1, 1))
    assert resolve('this_quarter', date(2026, 3, 31))[0] == date(2026, 1, 1)
    assert resolve('this_quarter', date(2026, 4, 1))[0] == date(2026, 4, 1)
    assert resolve('this_quarter', date(2026, 12, 31))[0] == date(2026, 10, 1)


def test_year_edges():
    assert resolve('this_year', date(2026, 1, 1)) == (date(2026, 1, 1), date(2026, 1, 1))
    assert resolve('this_year', date(2026, 12, 31)) == (date(2026, 1, 1), date(2026, 12, 31))


def test_last_12_months_from_a_leap_day_uses_the_shorter_month():
    assert resolve('last_12_months', date(2028, 2, 29)) == (date(2027, 3, 1), date(2028, 2, 29))


def test_all_time_starts_at_the_first_transaction_or_today():
    assert resolve('all_time', TODAY, first_transaction=date(2025, 9, 14)) == (
        date(2025, 9, 14),
        TODAY,
    )
    assert resolve('all_time', TODAY) == (TODAY, TODAY)


def test_custom_uses_the_dates():
    assert resolve('custom', TODAY, date(2026, 9, 1), date(2026, 9, 30)) == (
        date(2026, 9, 1),
        date(2026, 9, 30),
    )


def test_custom_with_the_ends_swapped_is_put_right():
    assert resolve('custom', TODAY, date(2026, 9, 30), date(2026, 9, 1)) == (
        date(2026, 9, 1),
        date(2026, 9, 30),
    )


def test_custom_open_ended_starts_at_the_first_transaction_and_ends_today():
    assert resolve('custom', TODAY, None, None, date(2025, 9, 14)) == (date(2025, 9, 14), TODAY)
    assert resolve('custom', TODAY, date(2026, 11, 1), None) == (date(2026, 11, 1), TODAY)


def test_custom_end_in_the_future_is_cut_to_today():
    assert resolve('custom', TODAY, date(2026, 11, 1), date(2027, 1, 1)) == (
        date(2026, 11, 1),
        TODAY,
    )


def test_custom_wholly_in_the_future_is_today():
    assert resolve('custom', TODAY, date(2027, 1, 1), date(2027, 2, 1)) == (TODAY, TODAY)


def test_nothing_starts_before_the_minimum_date():
    assert resolve('custom', TODAY, date(1900, 1, 1), date(1950, 1, 1))[0] == MIN_DATE


def test_an_unknown_key_is_an_error():
    with pytest.raises(ValueError):
        resolve('nope', TODAY)


def test_needs_first_transaction():
    assert needs_first_transaction('all_time', None)
    assert needs_first_transaction('custom', None)
    assert not needs_first_transaction('custom', date(2026, 1, 1))
    assert not needs_first_transaction('last_7_days', None)


@pytest.mark.parametrize(
    ('days', 'group'),
    [
        (1, 'day'),
        (31, 'day'),
        (32, 'week'),
        (190, 'week'),
        (191, 'month'),
        (1100, 'month'),
        (1101, 'year'),
    ],
)
def test_the_auto_grouping_thresholds(days, group):
    first = date(2026, 1, 1)
    last = date.fromordinal(first.toordinal() + days - 1)
    assert auto_group(first, last) == group


def test_weeks_start_on_monday():
    assert period_start(date(2026, 11, 22), 'week') == date(2026, 11, 16)  # a Sunday
    assert period_start(date(2026, 11, 23), 'week') == date(2026, 11, 23)  # a Monday


def test_period_starts_for_each_grouping():
    assert period_start(date(2026, 11, 23), 'month') == date(2026, 11, 1)
    assert period_start(date(2026, 11, 23), 'quarter') == date(2026, 10, 1)
    assert period_start(date(2026, 11, 23), 'year') == date(2026, 1, 1)
    assert next_start(date(2026, 12, 1), 'month') == date(2027, 1, 1)
    assert next_start(date(2026, 10, 1), 'quarter') == date(2027, 1, 1)
    assert next_start(date(2026, 1, 1), 'year') == date(2027, 1, 1)
    assert next_start(date(2026, 11, 16), 'week') == date(2026, 11, 23)


@pytest.mark.parametrize('group', ['day', 'week', 'month', 'quarter', 'year'])
@pytest.mark.parametrize(
    ('first', 'last'),
    [
        (date(2026, 11, 23), date(2026, 11, 23)),
        (date(2026, 11, 1), date(2026, 11, 30)),
        (date(2025, 12, 28), date(2026, 1, 4)),
        (date(2024, 2, 1), date(2026, 11, 23)),
    ],
)
def test_count_periods_agrees_with_listing_them(group, first, last):
    assert count_periods(first, last, group) == len(period_starts(first, last, group))


def test_the_first_and_last_periods_may_be_partial_and_are_whole_periods():
    starts = period_starts(date(2026, 11, 18), date(2026, 12, 2), 'week')
    assert starts == [date(2026, 11, 16), date(2026, 11, 23), date(2026, 11, 30)]


def test_the_limit_error_names_the_count_and_the_unit():
    error = PeriodLimitError(MAX_PERIODS + 1, 'day')
    assert '121 days' in error.messages[0]
    assert 'too many to show' in error.messages[0]
