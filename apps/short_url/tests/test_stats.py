from datetime import date, timedelta

import pytest

from ..models import DailyLinkDimension
from ..services import rollup, stats
from .events import NOW, TODAY, add_event, at_day, days_ago
from .factories import create_link

pytestmark = pytest.mark.django_db


@pytest.fixture
def link(user):
    return create_link(user, click_count=4, bot_click_count=2)


def seed(link):
    """Three days of clicks: two rolled up, yesterday and today still raw."""
    add_event(link, at_day(days_ago(3)), visitor='a', referrer='t.co', country='DE')
    add_event(link, at_day(days_ago(3)), visitor='a', referrer='t.co', country='DE')
    add_event(link, at_day(days_ago(3)), visitor='b', referrer='', country='US', bot=True)
    add_event(link, at_day(days_ago(2)), visitor='c', referrer='news.org', country='DE')
    rollup.rollup_clicks(NOW - timedelta(days=1))  # through day -2
    add_event(link, at_day(days_ago(1)), visitor='d', referrer='t.co', country='FR')
    add_event(link, at_day(TODAY, 8), visitor='e', referrer='t.co', country='DE')
    add_event(link, at_day(TODAY, 9), visitor='f', bot=True, country='DE')


class TestTotals:
    def test_clicks_and_bots_are_the_counters_and_visitors_come_from_the_days(self, link):
        seed(link)
        totals = stats.totals(link, now=NOW)
        assert (totals.clicks, totals.bot_clicks) == (4, 2)  # the link's own counters
        assert totals.uniques == 4  # a, c, d, e: bots (b, f) left out; a counted once
        assert totals.filter_bots is True

    def test_with_the_filter_off_visitors_include_bots(self, link):
        seed(link)
        link.filter_bots = False
        assert stats.totals(link, now=NOW).uniques == 6

    def test_no_events_is_all_zeros(self, user):
        link = create_link(user)
        totals = stats.totals(link, now=NOW)
        assert (totals.clicks, totals.bot_clicks, totals.uniques) == (0, 0, 0)


class TestRangeStats:
    def test_rolled_up_and_raw_days_read_the_same_way(self, link):
        seed(link)
        result = stats.range_stats(link, 'all', now=NOW)
        by_day = {bucket.start: bucket.clicks for bucket in result.buckets}
        assert by_day == {
            days_ago(3): 2,  # two people; the bot is left out
            days_ago(2): 1,
            days_ago(1): 1,  # raw: not rolled up yet
            TODAY: 1,  # raw: today
        }
        assert result.clicks == 5
        assert result.start == days_ago(3)

    def test_the_numbers_do_not_depend_on_how_far_the_rollup_got(self, link):
        """Yesterday is exact whether or not the hourly job has run since midnight."""
        seed(link)
        before = stats.range_stats(link, '30', now=NOW)
        rollup.rollup_clicks(NOW)
        after = stats.range_stats(link, '30', now=NOW)
        assert [(b.start, b.clicks) for b in before.buckets] == [
            (b.start, b.clicks) for b in after.buckets
        ]
        assert [[(r.value, r.count) for r in t.rows] for t in before.tables] == [
            [(r.value, r.count) for r in t.rows] for t in after.tables
        ]
        assert (before.clicks, before.uniques) == (after.clicks, after.uniques)

    def test_the_filter_decides_whether_bots_are_in(self, link):
        seed(link)
        link.filter_bots = False
        result = stats.range_stats(link, 'all', now=NOW)
        assert result.clicks == 7
        assert result.bot_clicks == 2
        assert {b.start: b.clicks for b in result.buckets}[days_ago(3)] == 3

    def test_a_range_cuts_off_older_days_and_fills_gaps_with_zeros(self, link):
        add_event(link, at_day(days_ago(20)))
        add_event(link, at_day(days_ago(1)))
        result = stats.range_stats(link, '7', now=NOW)
        assert result.clicks == 1
        assert len(result.buckets) == 7
        assert result.buckets[0].start == days_ago(6) and result.buckets[-1].start == TODAY
        assert [b.clicks for b in result.buckets] == [0, 0, 0, 0, 0, 1, 0]

    def test_all_time_starts_at_the_first_day_with_clicks(self, link):
        add_event(link, at_day(days_ago(40)))
        result = stats.range_stats(link, 'all', now=NOW)
        assert result.start == days_ago(40)
        assert len(result.buckets) == 41

    def test_a_link_without_clicks_still_has_a_chart(self, user):
        result = stats.range_stats(create_link(user), '30', now=NOW)
        assert len(result.buckets) == 30 and result.clicks == 0
        assert all(table.rows == [] for table in result.tables)

    def test_unknown_range_falls_back_to_the_default(self):
        assert stats.parse_range('7') == '7'
        assert stats.parse_range('all') == 'all'
        assert stats.parse_range('365') == stats.DEFAULT_RANGE
        assert stats.parse_range(None) == stats.DEFAULT_RANGE

    def test_visitors_are_summed_per_day(self, link):
        add_event(link, at_day(days_ago(2)), visitor='a')
        add_event(link, at_day(days_ago(1)), visitor='a')
        assert stats.range_stats(link, '30', now=NOW).uniques == 2


class TestBuckets:
    @pytest.mark.parametrize(
        ('span', 'size'), [(1, 1), (92, 1), (93, 7), (730, 7), (731, 0), (2000, 0)]
    )
    def test_the_bar_size_grows_with_the_span(self, span, size):
        assert stats.bucket_days_for(span) == size

    def test_weeks_start_on_monday_and_months_on_the_first(self):
        wednesday = date(2026, 10, 21)
        assert stats._bucket_start(wednesday, 7) == date(2026, 10, 19)
        assert stats._bucket_start(wednesday, 0) == date(2026, 10, 1)
        assert stats._next_bucket(date(2026, 12, 1), 0) == date(2027, 1, 1)
        assert stats._next_bucket(date(2026, 1, 31).replace(day=1), 0) == date(2026, 2, 1)

    def test_a_long_history_is_drawn_by_week(self, link):
        add_event(link, at_day(days_ago(200)))
        add_event(link, at_day(days_ago(1)))
        result = stats.range_stats(link, 'all', now=NOW)
        assert result.bucket_days == 7
        assert sum(b.clicks for b in result.buckets) == 2
        assert len(result.buckets) < 40

    def test_a_very_long_history_is_drawn_by_month(self, link):
        add_event(link, at_day(days_ago(1200)))
        result = stats.range_stats(link, 'all', now=NOW)
        assert result.bucket_days == 0
        assert len(result.buckets) < 50
        assert all(b.start.day == 1 for b in result.buckets)


class TestTopTables:
    def table(self, result, kind):
        return next(t for t in result.tables if t.kind == kind)

    def test_each_table_is_sorted_with_percentages_and_labels(self, link):
        seed(link)
        result = stats.range_stats(link, 'all', now=NOW)
        referrers = self.table(result, 'referrer')
        assert [(r.label, r.count, r.percent) for r in referrers.rows] == [
            ('t.co', 4, 80),
            ('news.org', 1, 20),
        ]
        countries = self.table(result, 'country')
        assert [(r.value, r.count) for r in countries.rows] == [('DE', 4), ('FR', 1)]

    def test_bots_are_left_out_of_the_tables_unless_the_filter_is_off(self, link):
        seed(link)
        on = {
            r.value: r.count
            for r in self.table(stats.range_stats(link, 'all', now=NOW), 'device').rows
        }
        assert 'bot' not in on and on == {'desktop': 5}
        link.filter_bots = False
        off = {
            r.value: r.count
            for r in self.table(stats.range_stats(link, 'all', now=NOW), 'device').rows
        }
        assert off == {'desktop': 5, 'bot': 2}

    def test_unknown_values_get_a_readable_label(self, link):
        add_event(link, at_day(days_ago(1)), referrer='', country='')
        result = stats.range_stats(link, '7', now=NOW)
        assert self.table(result, 'referrer').rows[0].label == 'Direct or unknown'
        assert self.table(result, 'country').rows[0].label == 'Unknown'
        assert self.table(result, 'device').rows[0].label == 'Desktop'

    def test_only_the_top_ten_are_listed(self, link):
        for n in range(15):
            add_event(link, at_day(days_ago(1)), referrer=f'site{n:02d}.com')
        rows = self.table(stats.range_stats(link, '7', now=NOW), 'referrer').rows
        assert len(rows) == stats.TOP_N

    def test_the_rolled_up_other_row_comes_last_and_is_labelled(self, link):
        day = days_ago(2)
        add_event(link, at_day(day), referrer='a.com')
        rollup.rollup_clicks(NOW)
        DailyLinkDimension.objects.create(
            link=link, date=day, kind='referrer', value=DailyLinkDimension.OTHER, count=50
        )
        rows = self.table(stats.range_stats(link, '7', now=NOW), 'referrer').rows
        assert rows[-1].label == 'Other' and rows[-1].count == 50
        assert rows[0].value == 'a.com'

    def test_percentages_add_up_to_the_listed_total(self, link):
        for host, n in (('a.com', 3), ('b.com', 1)):
            for _ in range(n):
                add_event(link, at_day(days_ago(1)), referrer=host)
        rows = self.table(stats.range_stats(link, '7', now=NOW), 'referrer').rows
        assert [(r.value, r.percent) for r in rows] == [('a.com', 75), ('b.com', 25)]


class TestChartBars:
    def bucket(self, clicks, day=1):
        return stats.Bucket(date(2026, 10, day), f'Oct {day}', clicks)

    def test_bars_are_scaled_to_the_tallest(self):
        bars = stats.chart_bars([self.bucket(10, 1), self.bucket(5, 2), self.bucket(0, 3)])
        assert bars[0].height == stats.CHART_HEIGHT
        assert bars[1].height == stats.CHART_HEIGHT / 2
        assert bars[2].height == 0
        assert all(bar.y + bar.height == stats.CHART_HEIGHT for bar in bars)

    def test_a_tiny_bar_stays_visible(self):
        bars = stats.chart_bars([self.bucket(1000, 1), self.bucket(1, 2)])
        assert bars[1].height >= 2

    def test_bars_fit_the_box_without_overlapping(self):
        bars = stats.chart_bars([self.bucket(n + 1, n + 1) for n in range(30)])
        assert bars[0].x >= 0
        assert bars[-1].x + bars[-1].width <= stats.CHART_WIDTH
        assert all(a.x + a.width <= b.x for a, b in zip(bars, bars[1:]))

    def test_no_buckets_no_bars(self):
        assert stats.chart_bars([]) == []

    def test_all_zero_does_not_divide_by_zero(self):
        assert [bar.height for bar in stats.chart_bars([self.bucket(0), self.bucket(0, 2)])] == [
            0,
            0,
        ]


def test_a_range_reads_the_watermark_once(link, monkeypatch):
    calls = []
    real = stats.rolled_up_through

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(stats, 'rolled_up_through', counting)
    stats.range_stats(link, stats.DEFAULT_RANGE, now=NOW)
    assert len(calls) == 1
