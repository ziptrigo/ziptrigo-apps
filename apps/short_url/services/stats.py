"""What the stats page shows: totals, clicks over time and the top referrers / countries /
devices / browsers of one link.

**Where the numbers come from.** Every complete day up to `rollup.rolled_up_through()` is read
from the rollup tables (`DailyLinkStat`, `DailyLinkDimension`, kept forever); the days after it
(yesterday until the next rollup, and today) are read from the raw events with the same
arithmetic, so the page is exact whatever the rollup's lag. All days are UTC dates.

**Bot filtering** follows the link's *current* `filter_bots`: on, every figure leaves bots out;
off, bots are added to the clicks, the visitors and the top lists. The raw data holds both, so
switching it re-reads the whole history that way (the counters on the link itself record the
choice in force at each click, see `clicks`).

**Unique visitors** are counted per day (a visitor is one hashed address of that day) and summed
over a range: someone who comes back on another day counts again.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone as dj_timezone

from ..models import ClickEvent, DailyLinkDimension, DailyLinkStat, DimensionKind, ShortLink
from .rollup import DIMENSION_FIELDS, day_start, rolled_up_through, stat_aggregates

#: The range selector: key -> days back from today (inclusive), None for all time.
RANGES: dict[str, int | None] = {'7': 7, '30': 30, '90': 90, 'all': None}
RANGE_LABELS = {'7': '7 days', '30': '30 days', '90': '90 days', 'all': 'All time'}
DEFAULT_RANGE = '30'

TOP_N = 10

KIND_TITLES = {
    DimensionKind.REFERRER: 'Top referrers',
    DimensionKind.COUNTRY: 'Top countries',
    DimensionKind.DEVICE: 'Devices',
    DimensionKind.BROWSER: 'Browsers',
}
_UNKNOWN_LABELS = {
    DimensionKind.REFERRER: 'Direct or unknown',
    DimensionKind.COUNTRY: 'Unknown',
    DimensionKind.DEVICE: 'Unknown',
    DimensionKind.BROWSER: 'Unknown',
}


def parse_range(value: str | None) -> str:
    """A range key from the query string, or the default for anything else."""
    return value if value in RANGES else DEFAULT_RANGE


@dataclass(frozen=True, slots=True)
class Day:
    date: date
    clicks: int
    bot_clicks: int
    uniques: int
    uniques_with_bots: int


@dataclass(frozen=True, slots=True)
class Totals:
    """The numbers that are always shown, whatever the link has paid for."""

    clicks: int
    bot_clicks: int
    uniques: int
    filter_bots: bool


@dataclass(frozen=True, slots=True)
class Bucket:
    """One bar of the chart: a day, a week (starting Monday) or a month of clicks."""

    start: date
    label: str
    clicks: int


@dataclass(frozen=True, slots=True)
class TopRow:
    value: str
    label: str
    count: int
    percent: int


@dataclass(frozen=True, slots=True)
class TopTable:
    kind: str
    title: str
    rows: list[TopRow]


@dataclass(frozen=True, slots=True)
class RangeStats:
    key: str
    start: date
    end: date
    clicks: int
    uniques: int
    bot_clicks: int
    bucket_days: int
    buckets: list[Bucket]
    tables: list[TopTable]


def _today(now: datetime | None) -> date:
    return (now or dj_timezone.now()).astimezone(timezone.utc).date()


def _days(link: ShortLink, start: date | None, end: date) -> list[Day]:
    """The link's per-day totals from `start` (None: from the beginning) through `end`, days
    without clicks left out: rollup rows up to the newest rolled-up day, events after it."""
    through = rolled_up_through()
    found: dict[date, Day] = {}

    rolled = DailyLinkStat.objects.filter(link=link, date__lte=min(end, through or date.min))
    if start is not None:
        rolled = rolled.filter(date__gte=start)
    if through is not None:
        for row in rolled:
            found[row.date] = Day(
                row.date, row.clicks, row.bot_clicks, row.uniques, row.uniques_with_bots
            )

    raw_from = day_start(through + timedelta(1)) if through is not None else None
    if start is not None:
        raw_from = max(raw_from, day_start(start)) if raw_from else day_start(start)
    raw = ClickEvent.objects.filter(link=link, at__lt=day_start(end + timedelta(1)))
    if raw_from is not None:
        raw = raw.filter(at__gte=raw_from)
    for row in (
        raw.annotate(day=TruncDate('at', tzinfo=timezone.utc))
        .order_by()
        .values('day')
        .annotate(**stat_aggregates())
    ):
        found[row['day']] = Day(
            row['day'], row['clicks'], row['bot_clicks'], row['uniques'], row['uniques_with_bots']
        )
    return [found[day] for day in sorted(found)]


def _clicks(day: Day, filter_bots: bool) -> int:
    return day.clicks if filter_bots else day.clicks + day.bot_clicks


def _uniques(day: Day, filter_bots: bool) -> int:
    return day.uniques if filter_bots else day.uniques_with_bots


def totals(link: ShortLink, *, now: datetime | None = None) -> Totals:
    """The always-visible totals. Clicks and bot clicks are the link's own counters (what the
    dashboard and the click cap use); unique visitors are summed from the daily figures."""
    days = _days(link, None, _today(now))
    return Totals(
        clicks=link.click_count,
        bot_clicks=link.bot_click_count,
        uniques=sum(_uniques(day, link.filter_bots) for day in days),
        filter_bots=link.filter_bots,
    )


def bucket_days_for(span_days: int) -> int:
    """How many days one bar covers so the chart stays readable: a day up to 92 days, a week up
    to two years, then months (0 stands for a calendar month)."""
    if span_days <= 92:
        return 1
    if span_days <= 730:
        return 7
    return 0


def _bucket_start(day: date, bucket_days: int) -> date:
    if bucket_days == 1:
        return day
    if bucket_days == 7:
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _next_bucket(start: date, bucket_days: int) -> date:
    if bucket_days:
        return start + timedelta(days=bucket_days)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _bucket_label(start: date, bucket_days: int) -> str:
    if bucket_days == 0:
        return start.strftime('%b %Y')
    return f'{start:%b} {start.day}'


def build_buckets(days: list[Day], start: date, end: date, filter_bots: bool) -> list[Bucket]:
    """The chart's bars from `start` through `end`, with the empty ones in between as zeros."""
    bucket_days = bucket_days_for((end - start).days + 1)
    per_bucket: dict[date, int] = defaultdict(int)
    for day in days:
        per_bucket[_bucket_start(day.date, bucket_days)] += _clicks(day, filter_bots)
    buckets: list[Bucket] = []
    current = _bucket_start(start, bucket_days)
    while current <= end:
        buckets.append(Bucket(current, _bucket_label(current, bucket_days), per_bucket[current]))
        current = _next_bucket(current, bucket_days)
    return buckets


def _label(kind: str, value: str) -> str:
    if value == DailyLinkDimension.OTHER:
        return 'Other'
    if not value:
        return _UNKNOWN_LABELS[DimensionKind(kind)]
    if kind == DimensionKind.DEVICE:
        return value.capitalize()
    return value


def top_table(
    link: ShortLink, kind: str, start: date, end: date, *, limit: int = TOP_N
) -> TopTable:
    """The `limit` biggest values of one dimension in the range, largest first (`Other` last)."""
    through = rolled_up_through()
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # value -> [humans, bots]

    if through is not None and start <= through:
        for row in (
            DailyLinkDimension.objects.filter(
                link=link, kind=kind, date__gte=start, date__lte=min(end, through)
            )
            .order_by()
            .values('value')
            .annotate(c=Sum('count'), b=Sum('bot_count'))
        ):
            counts[row['value']][0] += row['c']
            counts[row['value']][1] += row['b']

    raw_start = max(start, through + timedelta(1)) if through is not None else start
    if raw_start <= end:
        field = DIMENSION_FIELDS[DimensionKind(kind)]
        for row in (
            ClickEvent.objects.filter(
                link=link, at__gte=day_start(raw_start), at__lt=day_start(end + timedelta(1))
            )
            .order_by()
            .values(field)
            .annotate(c=Count('id', filter=Q(is_bot=False)), b=Count('id', filter=Q(is_bot=True)))
        ):
            counts[row[field]][0] += row['c']
            counts[row[field]][1] += row['b']

    scored = [
        (value, humans if link.filter_bots else humans + bots)
        for value, (humans, bots) in counts.items()
    ]
    scored = [(value, n) for value, n in scored if n > 0]
    total = sum(n for _, n in scored)
    other = [item for item in scored if item[0] == DailyLinkDimension.OTHER]
    named = sorted(
        (item for item in scored if item[0] != DailyLinkDimension.OTHER),
        key=lambda item: (-item[1], item[0]),
    )
    ordered = (named[:limit] + other)[: limit + 1] if other else named[:limit]
    rows = [
        TopRow(value, _label(kind, value), n, round(100 * n / total) if total else 0)
        for value, n in ordered
    ]
    return TopTable(kind, KIND_TITLES[DimensionKind(kind)], rows)


def range_stats(
    link: ShortLink, key: str = DEFAULT_RANGE, *, now: datetime | None = None
) -> RangeStats:
    """Everything the detailed page shows for one range (`RANGES`)."""
    key = parse_range(key)
    end = _today(now)
    days_back = RANGES[key]
    if days_back is not None:
        start = end - timedelta(days=days_back - 1)
        days = _days(link, start, end)
    else:
        days = _days(link, None, end)
        start = days[0].date if days else end
    buckets = build_buckets(days, start, end, link.filter_bots)
    return RangeStats(
        key=key,
        start=start,
        end=end,
        clicks=sum(_clicks(day, link.filter_bots) for day in days),
        uniques=sum(_uniques(day, link.filter_bots) for day in days),
        bot_clicks=sum(day.bot_clicks for day in days),
        bucket_days=bucket_days_for((end - start).days + 1),
        buckets=buckets,
        tables=[top_table(link, kind, start, end) for kind in DIMENSION_FIELDS],
    )


@dataclass(frozen=True, slots=True)
class Bar:
    """One bar of the SVG chart, in the chart's own coordinates."""

    x: float
    y: float
    width: float
    height: float
    label: str
    clicks: int


CHART_WIDTH = 600
CHART_HEIGHT = 160


def chart_bars(buckets: list[Bucket]) -> list[Bar]:
    """The bars for `buckets`, scaled to the tallest in a `CHART_WIDTH` x `CHART_HEIGHT` box
    (a bar that has clicks is at least 2 units tall, so it never vanishes)."""
    if not buckets:
        return []
    peak = max(bucket.clicks for bucket in buckets) or 1
    slot = CHART_WIDTH / len(buckets)
    width = max(1.0, slot * 0.78)
    bars = []
    for index, bucket in enumerate(buckets):
        height = max(2.0, CHART_HEIGHT * bucket.clicks / peak) if bucket.clicks else 0.0
        bars.append(
            Bar(
                x=round(index * slot + (slot - width) / 2, 2),
                y=round(CHART_HEIGHT - height, 2),
                width=round(width, 2),
                height=round(height, 2),
                label=bucket.label,
                clicks=bucket.clicks,
            )
        )
    return bars
