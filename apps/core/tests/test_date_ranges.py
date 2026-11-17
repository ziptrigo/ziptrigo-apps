"""The date ranges the account page's export blocks share (`apps.core.date_ranges`)."""

from datetime import date

import pytest

from ..date_ranges import DATE_RANGE_ANY, DATE_RANGE_CUSTOM, DATE_RANGES, resolve_date_range

pytestmark = [pytest.mark.unit]


def test_the_options_are_any_date_first_and_custom_last():
    keys = [key for key, _label in DATE_RANGES]
    assert keys[0] == DATE_RANGE_ANY and keys[-1] == DATE_RANGE_CUSTOM
    assert keys == [
        'any',
        'last_15_days',
        'last_30_days',
        'this_month',
        'this_quarter',
        'this_year',
        'custom',
    ]


@pytest.mark.parametrize(
    ('key', 'today', 'expected'),
    [
        ('last_15_days', date(2026, 10, 12), (date(2026, 9, 28), date(2026, 10, 12))),
        ('last_30_days', date(2026, 10, 12), (date(2026, 9, 13), date(2026, 10, 12))),
        ('this_month', date(2026, 2, 12), (date(2026, 2, 1), date(2026, 2, 28))),
        ('this_month', date(2028, 2, 12), (date(2028, 2, 1), date(2028, 2, 29))),
        ('this_quarter', date(2026, 5, 31), (date(2026, 4, 1), date(2026, 6, 30))),
        ('this_quarter', date(2026, 12, 1), (date(2026, 10, 1), date(2026, 12, 31))),
        ('this_year', date(2026, 10, 12), (date(2026, 1, 1), date(2026, 12, 31))),
    ],
)
def test_presets_resolve_against_today(key, today, expected):
    assert resolve_date_range(key, today) == expected


@pytest.mark.parametrize('key', ['any', 'custom', 'nope', ''])
def test_anything_else_has_no_dates_of_its_own(key):
    with pytest.raises(ValueError):
        resolve_date_range(key, date(2026, 1, 1))
