from datetime import timedelta

import pytest
from django.apps import apps

from apps.core.scheduler import registry

from .. import jobs
from ..models import ClickEvent, DailyLinkDimension, DailyLinkStat, ShortUrlSettings
from ..services import rollup
from .events import NOW, TODAY, add_event, at_day, days_ago
from .factories import create_link

pytestmark = pytest.mark.django_db


@pytest.fixture
def link(user):
    return create_link(user)


def stat(link, day):
    return DailyLinkStat.objects.get(link=link, date=day)


def dims(link, day, kind):
    return {
        (row.value, row.count, row.bot_count)
        for row in DailyLinkDimension.objects.filter(link=link, date=day, kind=kind)
    }


class TestRollupDay:
    def test_totals_split_people_from_bots_and_count_visitors_once(self, link):
        day = days_ago(1)
        add_event(link, at_day(day, 9), visitor='a')
        add_event(link, at_day(day, 10), visitor='a')  # the same visitor again
        add_event(link, at_day(day, 11), visitor='b')
        add_event(link, at_day(day, 12), visitor='c', bot=True)
        add_event(link, at_day(day, 13), visitor='a', bot=True)  # a person's address, a bot's click
        rollup.rollup_day(day)
        row = stat(link, day)
        assert (row.clicks, row.uniques) == (3, 2)
        assert (row.bot_clicks, row.uniques_with_bots) == (2, 3)

    def test_each_dimension_gets_its_counts_with_bots_apart(self, link):
        day = days_ago(1)
        add_event(
            link, at_day(day), referrer='t.co', country='DE', device='mobile', browser='Safari'
        )
        add_event(
            link, at_day(day), referrer='t.co', country='DE', device='mobile', browser='Safari'
        )
        add_event(link, at_day(day), referrer='', country='US')
        add_event(link, at_day(day), referrer='t.co', country='DE', bot=True)
        rollup.rollup_day(day)
        assert dims(link, day, 'referrer') == {('t.co', 2, 1), ('', 1, 0)}
        assert dims(link, day, 'country') == {('DE', 2, 1), ('US', 1, 0)}
        assert dims(link, day, 'device') == {('mobile', 2, 0), ('desktop', 1, 0), ('bot', 0, 1)}
        assert dims(link, day, 'browser') == {('Safari', 2, 0), ('Chrome', 1, 1)}

    def test_only_that_days_events_count(self, link):
        add_event(link, at_day(days_ago(2), 23))
        add_event(link, at_day(days_ago(1), 0))
        add_event(link, at_day(days_ago(1), 23))
        add_event(link, at_day(days_ago(0), 0))
        rollup.rollup_day(days_ago(1))
        assert list(DailyLinkStat.objects.values_list('date', 'clicks')) == [(days_ago(1), 2)]

    def test_each_link_gets_its_own_rows(self, link, user):
        other = create_link(user)
        day = days_ago(1)
        add_event(link, at_day(day))
        add_event(other, at_day(day))
        add_event(other, at_day(day))
        assert rollup.rollup_day(day) == 2
        assert (stat(link, day).clicks, stat(other, day).clicks) == (1, 2)

    def test_it_rewrites_the_day_so_a_rerun_gives_the_same_rows(self, link):
        day = days_ago(1)
        add_event(link, at_day(day), referrer='a.com')
        rollup.rollup_day(day)
        before = (
            list(DailyLinkStat.objects.values_list('clicks', 'uniques')),
            sorted(DailyLinkDimension.objects.values_list('kind', 'value', 'count')),
        )
        rollup.rollup_day(day)
        rollup.rollup_day(day)
        after = (
            list(DailyLinkStat.objects.values_list('clicks', 'uniques')),
            sorted(DailyLinkDimension.objects.values_list('kind', 'value', 'count')),
        )
        assert before == after
        assert DailyLinkStat.objects.count() == 1

    def test_a_late_event_is_picked_up_by_a_rerun(self, link):
        day = days_ago(1)
        add_event(link, at_day(day))
        rollup.rollup_day(day)
        add_event(link, at_day(day, 23))
        rollup.rollup_day(day)
        assert stat(link, day).clicks == 2

    def test_values_beyond_the_top_fifty_fold_into_other(self, link, monkeypatch):
        monkeypatch.setattr(rollup, 'MAX_VALUES_PER_KIND', 3)
        day = days_ago(1)
        for host, times in (('a.com', 4), ('b.com', 3), ('c.com', 2), ('d.com', 1), ('e.com', 1)):
            for _ in range(times):
                add_event(link, at_day(day), referrer=host)
        rollup.rollup_day(day)
        assert dims(link, day, 'referrer') == {
            ('a.com', 4, 0),
            ('b.com', 3, 0),
            (DailyLinkDimension.OTHER, 4, 0),
        }

    def test_a_day_without_events_writes_nothing(self, link):
        assert rollup.rollup_day(days_ago(1)) == 0
        assert not DailyLinkStat.objects.exists()


class TestRollupClicks:
    def test_it_rolls_up_complete_days_and_leaves_today_alone(self, link):
        add_event(link, at_day(days_ago(3)))
        add_event(link, at_day(days_ago(1)))
        add_event(link, at_day(TODAY, 8))
        assert rollup.rollup_clicks(NOW) == 2
        assert sorted(DailyLinkStat.objects.values_list('date', flat=True)) == [
            days_ago(3),
            days_ago(1),
        ]

    def test_a_second_run_changes_nothing(self, link):
        add_event(link, at_day(days_ago(2)))
        add_event(link, at_day(days_ago(1)))
        rollup.rollup_clicks(NOW)
        rollup.rollup_clicks(NOW)
        assert DailyLinkStat.objects.count() == 2
        assert sorted(DailyLinkStat.objects.values_list('clicks', flat=True)) == [1, 1]

    def test_it_continues_from_the_newest_rolled_up_day(self, link):
        add_event(link, at_day(days_ago(5)))
        add_event(link, at_day(days_ago(4)))
        rollup.rollup_clicks(NOW - timedelta(days=3))  # as if run three days ago
        DailyLinkStat.objects.filter(date=days_ago(5)).update(clicks=99)  # marks it as done
        add_event(link, at_day(days_ago(2)))
        rollup.rollup_clicks(NOW)
        assert stat(link, days_ago(5)).clicks == 99, 'an older complete day is not recomputed'
        assert stat(link, days_ago(4)).clicks == 1
        assert stat(link, days_ago(2)).clicks == 1

    def test_the_newest_rolled_up_day_is_recomputed_to_catch_stragglers(self, link):
        add_event(link, at_day(days_ago(1)))
        rollup.rollup_clicks(NOW)
        add_event(link, at_day(days_ago(1), 23))  # landed just after the run
        rollup.rollup_clicks(NOW)
        assert stat(link, days_ago(1)).clicks == 2

    def test_it_stops_at_a_day_it_cannot_write(self, link, monkeypatch):
        add_event(link, at_day(days_ago(3)))
        add_event(link, at_day(days_ago(2)))
        real = rollup.rollup_day

        def fail_on_the_second(day):
            if day == days_ago(2):
                raise RuntimeError('boom')
            return real(day)

        monkeypatch.setattr(rollup, 'rollup_day', fail_on_the_second)
        with pytest.raises(RuntimeError):
            rollup.rollup_clicks(NOW)
        # The newest rolled-up day did not move past the failed one, so the next run retries it.
        assert rollup.rolled_up_through() == days_ago(3)
        monkeypatch.setattr(rollup, 'rollup_day', real)
        rollup.rollup_clicks(NOW)
        assert stat(link, days_ago(2)).clicks == 1

    def test_with_no_events_it_does_nothing(self, link):
        assert rollup.rollup_clicks(NOW) == 0


class TestPurge:
    def _setup(self, link):
        for n in (120, 100, 91, 90, 60, 1):
            add_event(link, at_day(days_ago(n)))
        rollup.rollup_clicks(NOW)

    def test_events_older_than_the_retention_go_and_their_totals_stay(self, link):
        self._setup(link)
        deleted = rollup.purge_click_events(NOW)
        assert deleted == 3  # 120, 100 and 91 days ago; day 90 is the oldest kept
        left = sorted(ClickEvent.objects.values_list('at', flat=True))
        assert [event.date() for event in left] == [days_ago(90), days_ago(60), days_ago(1)]
        assert DailyLinkStat.objects.count() == 6, 'the rollups are kept forever'

    def test_the_retention_is_a_setting(self, link):
        self._setup(link)
        row = ShortUrlSettings.load()
        row.click_event_retention_days = 30
        row.save()
        rollup.purge_click_events(NOW)
        assert [e.date() for e in ClickEvent.objects.values_list('at', flat=True)] == [days_ago(1)]

    def test_whole_days_only(self, link):
        """The cutoff is the start of a UTC day, so a day is never kept half."""
        add_event(link, at_day(days_ago(91), 1))
        add_event(link, at_day(days_ago(91), 23))
        add_event(link, at_day(days_ago(90), 0))
        rollup.rollup_clicks(NOW)
        rollup.purge_click_events(NOW)
        assert ClickEvent.objects.count() == 1

    def test_nothing_goes_while_nothing_is_rolled_up(self, link):
        add_event(link, at_day(days_ago(200)))
        assert rollup.purge_click_events(NOW) == 0
        assert ClickEvent.objects.count() == 1

    def test_days_after_the_newest_rollup_are_kept_even_when_old(self, link):
        add_event(link, at_day(days_ago(250)))
        add_event(link, at_day(days_ago(200)))
        rollup.rollup_clicks(NOW - timedelta(days=150))  # newest rolled-up day: 200 days ago
        add_event(link, at_day(days_ago(120)))  # never rolled up: the job was down
        rollup.purge_click_events(NOW)
        assert sorted(e.date() for e in ClickEvent.objects.values_list('at', flat=True)) == [
            days_ago(200),  # the newest rolled-up day itself stays too
            days_ago(120),
        ]

    def test_the_newest_rolled_up_day_is_never_purged(self, link):
        """With a retention of 1 day, just after midnight and before the first rollup of the new
        day, the cutoff reaches the day the rollup rewrites: it must keep its events, or the
        rewrite would undercount it for good."""
        row = ShortUrlSettings.load()
        row.click_event_retention_days = 1
        row.save()
        add_event(link, at_day(days_ago(3)))
        add_event(link, at_day(days_ago(2)))
        add_event(link, at_day(days_ago(2), 15))
        early = at_day(TODAY, 0).replace(minute=5)
        rollup.rollup_clicks(early - timedelta(days=1))  # watermark: the day before yesterday
        assert DailyLinkStat.objects.latest('date').date == days_ago(2)
        rollup.purge_click_events(early)
        days = sorted(e.date() for e in ClickEvent.objects.values_list('at', flat=True))
        assert days == [days_ago(2), days_ago(2)], 'only the older, safely rolled-up day went'
        rollup.rollup_clicks(early)
        assert stat(link, days_ago(2)).clicks == 2

    def test_it_deletes_in_batches(self, link, monkeypatch):
        monkeypatch.setattr(rollup, 'PURGE_BATCH', 2)
        for _ in range(5):
            add_event(link, at_day(days_ago(100)))
        add_event(link, at_day(days_ago(50)))  # a later rolled-up day, so day -100 is safe
        rollup.rollup_clicks(NOW)
        assert rollup.purge_click_events(NOW) == 5
        assert ClickEvent.objects.count() == 1  # the day -50 event is inside the retention

    def test_a_run_is_bounded_and_the_next_one_carries_on(self, link, monkeypatch):
        monkeypatch.setattr(rollup, 'PURGE_BATCH', 2)
        monkeypatch.setattr(rollup, 'PURGE_MAX_BATCHES', 1)
        for _ in range(5):
            add_event(link, at_day(days_ago(100)))
        add_event(link, at_day(days_ago(50)))  # a later rolled-up day, so day -100 is safe
        rollup.rollup_clicks(NOW)
        assert rollup.purge_click_events(NOW) == 2
        assert rollup.purge_click_events(NOW) == 2
        assert rollup.purge_click_events(NOW) == 1


class TestJobs:
    def test_both_jobs_are_registered_with_the_scheduler(self):
        """What `AppConfig.ready()` registers (other tests clear the registry, so run it again)."""
        saved = dict(registry._registry)
        registry.clear_registry()
        try:
            apps.get_app_config('short_url')._register_jobs()
            specs = {spec.name: spec for spec in registry.get_jobs()}
        finally:
            registry._registry.update(saved)
        assert specs['short_url.rollup_clicks'].interval == timedelta(hours=1)
        assert specs['short_url.rollup_clicks'].func is jobs.rollup_clicks
        assert specs['short_url.purge_click_events'].interval == timedelta(hours=24)
        assert specs['short_url.purge_click_events'].func is jobs.purge_click_events

    def test_the_jobs_run_the_services(self, link):
        add_event(link, at_day(days_ago(1)))
        add_event(link, at_day(days_ago(200)))
        jobs.rollup_clicks()
        jobs.purge_click_events()  # idempotent, and nothing to fail on
        assert DailyLinkStat.objects.count() == 2

    def test_a_failing_rollup_propagates_so_the_runner_records_it(self, monkeypatch):
        def boom():
            raise ValueError('x')

        monkeypatch.setattr(rollup, 'rollup_clicks', boom)
        with pytest.raises(ValueError):
            jobs.rollup_clicks()
