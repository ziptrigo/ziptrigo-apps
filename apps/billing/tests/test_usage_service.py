"""The credit usage report's numbers (issue #347)."""

from datetime import date

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.billing.models import CreditTransactionType
from apps.billing.services import usage
from apps.billing.services.usage_ranges import PeriodLimitError
from apps.core.datetime_format import CLOCK_24H, DMY, PUBLIC, DisplayFormats

from .usage_support import ledger, make_user, refund, spend, utc

pytestmark = pytest.mark.django_db


def _report(user, first, last, group='day', **kwargs):
    return usage.build_report(user, first=first, last=last, group=group, **kwargs)


def _by(report):
    return {app.label: app.used for app in report.apps}


def test_spends_are_credits_used_per_app():
    user = make_user()
    spend(user, 4, 'file_transfer', utc(2026, 11, 20))
    spend(user, 1, 'receipts', utc(2026, 11, 20))
    spend(user, 6, 'receipts', utc(2026, 11, 21))

    report = _report(user, date(2026, 11, 19), date(2026, 11, 22))

    assert _by(report) == {'file_transfer': 4, 'receipts': 7}
    assert report.used == 11
    assert [p.start for p in report.periods] == [
        date(2026, 11, 19),
        date(2026, 11, 20),
        date(2026, 11, 21),
        date(2026, 11, 22),
    ]
    assert [p.total for p in report.periods] == [0, 5, 6, 0]  # empty days are there, as zero
    assert report.periods[1].amounts == {'file_transfer': 4, 'receipts': 1}


def test_a_refund_is_subtracted_in_the_period_it_was_made():
    user = make_user()
    spend(user, 10, 'receipts', utc(2026, 11, 10))
    refund(user, 4, 'receipts', utc(2026, 11, 12))

    report = _report(user, date(2026, 11, 10), date(2026, 11, 12))

    assert [p.total for p in report.periods] == [10, 0, -4]  # a period can be negative
    assert report.used == 6
    assert report.refunded == 4
    assert report.peak == 10


def test_a_period_with_only_a_refund_is_negative_and_the_peak_stays_zero():
    user = make_user()
    refund(user, 3, 'receipts', utc(2026, 11, 12))

    report = _report(user, date(2026, 11, 12), date(2026, 11, 12))

    assert report.periods[0].total == -3
    assert report.used == -3
    assert report.peak == 0


def test_credits_that_came_in_are_not_usage_but_are_summed_by_type():
    user = make_user()
    ledger(user, 500, kind=CreditTransactionType.PROMO, source='billing', at=utc(2026, 11, 3))
    ledger(user, 1000, kind=CreditTransactionType.WELCOME, source='billing', at=utc(2026, 11, 2))
    ledger(user, 200, kind=CreditTransactionType.PURCHASE, at=utc(2026, 11, 4))
    ledger(user, 50, kind=CreditTransactionType.ADJUSTMENT, at=utc(2026, 11, 5))
    spend(user, 5, 'todo', utc(2026, 11, 6))
    refund(user, 2, 'todo', utc(2026, 11, 7))

    report = _report(user, date(2026, 11, 1), date(2026, 11, 30), 'month')

    assert report.used == 3
    assert [(a.type, a.credits) for a in report.added] == [
        ('purchase', 200),
        ('adjustment', 50),
        ('promo', 500),
        ('welcome', 1000),
    ]
    assert report.added_total == 1750
    assert [a.label for a in report.added][0] == 'Purchase'


def test_a_negative_adjustment_is_usage_in_other():
    user = make_user()
    ledger(user, -25, kind=CreditTransactionType.ADJUSTMENT, at=utc(2026, 11, 9))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 9))

    assert _by(report) == {'other': 25}
    assert report.apps[0].name == 'Other'
    assert report.apps[0].slot is None


def test_a_spend_with_no_source_or_an_unknown_one_is_other_and_other_is_last():
    user = make_user()
    spend(user, 2, '', utc(2026, 11, 9))
    spend(user, 3, 'gone_app', utc(2026, 11, 9))
    spend(user, 1, 'todo', utc(2026, 11, 9))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 9))

    assert [a.label for a in report.apps] == ['todo', 'other']
    assert _by(report) == {'todo': 1, 'other': 5}


def test_apps_come_in_registry_order_with_their_chart_slot():
    user = make_user()
    labels = list(usage.registry())
    for label in reversed(labels[:3]):
        spend(user, 1, label, utc(2026, 11, 9))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 9))

    assert [a.label for a in report.apps] == labels[:3]
    assert [a.slot for a in report.apps] == [1, 2, 3]
    assert report.apps[0].color == 'var(--c-chart-1)'


def test_an_app_without_usage_is_not_listed():
    user = make_user()
    spend(user, 1, 'todo', utc(2026, 11, 9))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 9))

    assert [a.label for a in report.apps] == ['todo']


def test_other_users_rows_are_never_counted():
    user, other = make_user(), make_user()
    spend(other, 99, 'receipts', utc(2026, 11, 9))
    ledger(other, 500, kind=CreditTransactionType.PROMO, at=utc(2026, 11, 9))
    spend(user, 1, 'receipts', utc(2026, 11, 9))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 9))

    assert report.used == 1
    assert report.added == ()


def test_rows_outside_the_interval_are_left_out():
    user = make_user()
    spend(user, 1, 'todo', utc(2026, 11, 8, 23, 59))
    spend(user, 2, 'todo', utc(2026, 11, 9, 0, 0))
    spend(user, 4, 'todo', utc(2026, 11, 10, 0, 0))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 9))

    assert report.used == 2


def test_days_are_the_accounts_not_utcs():
    user = make_user(timezone='America/Los_Angeles')  # UTC-8 in November
    spend(user, 3, 'todo', utc(2026, 11, 10, 7, 30))  # 23:30 on the 9th in Los Angeles
    spend(user, 5, 'todo', utc(2026, 11, 10, 8, 30))  # 00:30 on the 10th

    report = _report(user, date(2026, 11, 9), date(2026, 11, 10))

    assert [(p.start, p.total) for p in report.periods] == [
        (date(2026, 11, 9), 3),
        (date(2026, 11, 10), 5),
    ]


def test_a_day_with_a_clock_change_is_one_period():
    user = make_user(timezone='America/Los_Angeles')  # clocks go back on 2026-11-01
    spend(user, 1, 'todo', utc(2026, 11, 1, 7, 10))  # 00:10 PDT
    spend(user, 2, 'todo', utc(2026, 11, 1, 9, 30))  # 01:30 PST (the repeated hour)
    spend(user, 4, 'todo', utc(2026, 11, 2, 7, 59))  # 23:59 PST on the 1st
    spend(user, 8, 'todo', utc(2026, 11, 2, 8, 0))  # 00:00 PST on the 2nd

    report = _report(user, date(2026, 10, 31), date(2026, 11, 2))

    assert [(p.start, p.total) for p in report.periods] == [
        (date(2026, 10, 31), 0),
        (date(2026, 11, 1), 7),
        (date(2026, 11, 2), 8),
    ]


def test_the_interval_bounds_follow_the_accounts_zone():
    user = make_user(timezone='Asia/Tokyo')  # UTC+9
    spend(user, 1, 'todo', utc(2026, 11, 8, 14, 59))  # 23:59 on the 8th in Tokyo
    spend(user, 2, 'todo', utc(2026, 11, 8, 15, 0))  # 00:00 on the 9th

    assert _report(user, date(2026, 11, 9), date(2026, 11, 9)).used == 2


def test_weeks_start_on_monday_and_edge_weeks_are_whole_periods():
    user = make_user()
    spend(user, 1, 'todo', utc(2026, 11, 22))  # a Sunday
    spend(user, 2, 'todo', utc(2026, 11, 23))  # the next Monday
    spend(user, 4, 'todo', utc(2026, 11, 29))  # a Sunday

    report = _report(user, date(2026, 11, 18), date(2026, 11, 30), 'week')

    assert [(p.start, p.total) for p in report.periods] == [
        (date(2026, 11, 16), 1),
        (date(2026, 11, 23), 6),
        (date(2026, 11, 30), 0),
    ]


def test_months_quarters_and_years():
    user = make_user()
    spend(user, 1, 'todo', utc(2026, 1, 15))
    spend(user, 2, 'todo', utc(2026, 5, 15))
    spend(user, 4, 'todo', utc(2026, 6, 30))
    spend(user, 8, 'todo', utc(2027, 2, 1))

    months = _report(user, date(2026, 1, 1), date(2026, 6, 30), 'month')
    assert [p.total for p in months.periods] == [1, 0, 0, 0, 2, 4]

    quarters = _report(user, date(2026, 1, 1), date(2027, 3, 31), 'quarter')
    assert [(p.start, p.total) for p in quarters.periods] == [
        (date(2026, 1, 1), 1),
        (date(2026, 4, 1), 6),
        (date(2026, 7, 1), 0),
        (date(2026, 10, 1), 0),
        (date(2027, 1, 1), 8),
    ]

    years = _report(user, date(2026, 1, 1), date(2027, 12, 31), 'year')
    assert [(p.start, p.total) for p in years.periods] == [
        (date(2026, 1, 1), 7),
        (date(2027, 1, 1), 8),
    ]


def test_the_average_is_per_period_including_empty_ones():
    user = make_user()
    spend(user, 10, 'todo', utc(2026, 11, 9))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 12))

    assert report.average == 2.5
    assert len(report.periods) == 4


def test_more_than_120_periods_is_refused_before_any_query():
    user = make_user()
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(PeriodLimitError):
            _report(user, date(2026, 1, 1), date(2026, 11, 23), 'day')
    assert len(queries) == 0
    assert len(_report(user, date(2026, 8, 1), date(2026, 11, 28), 'day').periods) == 120


def test_the_app_filter_counts_only_those_apps_but_lists_all():
    user = make_user()
    spend(user, 4, 'file_transfer', utc(2026, 11, 9))
    spend(user, 6, 'receipts', utc(2026, 11, 9))
    spend(user, 1, '', utc(2026, 11, 9))
    refund(user, 2, 'receipts', utc(2026, 11, 9))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 9), apps=['receipts'])

    assert [(a.label, a.shown) for a in report.apps] == [
        ('file_transfer', False),
        ('receipts', True),
        ('other', False),
    ]
    assert report.used == 4
    assert report.refunded == 2
    assert report.periods[0].total == 4
    assert report.periods[0].amounts['file_transfer'] == 4  # still known, just not counted


def test_a_filter_naming_no_app_with_usage_shows_nothing_or_everything_when_lenient():
    user = make_user()
    spend(user, 4, 'file_transfer', utc(2026, 11, 9))

    strict = _report(user, date(2026, 11, 9), date(2026, 11, 9), apps=['todo'])
    lenient = _report(user, date(2026, 11, 9), date(2026, 11, 9), apps=['todo'], lenient=True)

    assert strict.used == 0 and strict.shown_apps == ()
    assert lenient.used == 4 and len(lenient.shown_apps) == 1


def test_added_ignores_the_app_filter():
    user = make_user()
    ledger(user, 500, kind=CreditTransactionType.PROMO, at=utc(2026, 11, 9))
    spend(user, 4, 'todo', utc(2026, 11, 9))

    report = _report(user, date(2026, 11, 9), date(2026, 11, 9), apps=['todo'])

    assert report.added_total == 500


def test_a_report_is_a_fixed_number_of_queries():
    user = make_user(timezone='Europe/Lisbon')
    for index in range(1, 25):
        spend(user, index, usage.registry().popitem()[0], utc(2026, 11, index))
        ledger(user, 5, kind=CreditTransactionType.PROMO, at=utc(2026, 11, index))

    with CaptureQueriesContext(connection) as queries:
        report = _report(user, date(2026, 11, 1), date(2026, 11, 30))

    assert len(queries) == 2  # the usage rows, the credits added
    assert report.used == sum(range(1, 25))


def test_first_transaction_date_is_in_the_accounts_zone():
    user = make_user(timezone='Asia/Tokyo')
    assert usage.first_transaction_date(user) is None
    spend(user, 1, 'todo', utc(2026, 11, 8, 16))  # the 9th in Tokyo
    spend(user, 1, 'todo', utc(2026, 11, 20))

    assert usage.first_transaction_date(user) == date(2026, 11, 9)


def test_period_labels_for_each_grouping():
    start = date(2026, 11, 16)
    assert usage.period_label(start, 'day', PUBLIC) == 'Nov 16, 2026'
    assert usage.period_label(start, 'week', PUBLIC) == 'Week of Nov 16, 2026'
    assert usage.period_label(start, 'day', DisplayFormats(DMY, CLOCK_24H)) == '16/11/2026'
    assert usage.period_label(start, 'day', PUBLIC, short=True) == 'Nov 16'
    assert usage.period_label(date(2026, 11, 1), 'month') == 'Nov 2026'
    assert usage.period_label(date(2026, 10, 1), 'quarter') == 'Q4 2026'
    assert usage.period_label(date(2026, 1, 1), 'year') == '2026'


def test_app_name_names_registered_products_only():
    assert usage.app_name('receipts') == 'Receipts'
    assert usage.app_name('') == ''
    assert usage.app_name('billing') == ''
    assert usage.app_name('gone_app') == ''
