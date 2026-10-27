"""Rolling click events up into daily totals, and purging old events.

Two scheduler jobs (`jobs.py`) call these; both are idempotent, since the scheduler is
at-least-once.

**Rollup.** `rollup_clicks` rewrites the totals of every *complete* UTC day that has events,
starting at the newest day already rolled up (`rolled_up_through`) and ending yesterday. Starting
at that day, not after it, repairs it if an event landed just after its last run; a day is
rewritten as a whole (the day's rows for the links that have events are deleted and recreated in
one transaction), so a re-run gives the same rows. If a day fails the run stops there and raises:
carrying on would move the newest rolled-up day past it and it would never be retried (the
runner records the error in `ScheduledJob.last_error`).

**Reading.** Everything up to `rolled_up_through` is in the rollup tables; the days after it
(yesterday, until the next run, and today) are read from the raw events (`stats`). That is why
the purge must never delete an event of a day that isn't rolled up yet.

**Purge.** `purge_click_events` deletes, in batches, the events of whole UTC days older than
`ShortUrlSettings.click_event_retention_days` (and only days already rolled up).
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from django.db import transaction
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate
from django.utils import timezone as dj_timezone

from ..models import (
    ClickEvent,
    DailyLinkDimension,
    DailyLinkStat,
    DimensionKind,
    ShortUrlSettings,
)

logger = logging.getLogger(__name__)

#: Most values kept per link, day and kind; the rest of the day's clicks are one `(other)` row,
#: so a flood of made-up `Referer` hosts can't grow the table without bound.
MAX_VALUES_PER_KIND = 50

#: Events deleted per statement, and statements per run (the next run carries on).
PURGE_BATCH = 5000
PURGE_MAX_BATCHES = 400

_DELETE_CHUNK = 500

#: Event field behind each dimension.
DIMENSION_FIELDS = {
    DimensionKind.REFERRER: 'referrer_host',
    DimensionKind.COUNTRY: 'country',
    DimensionKind.DEVICE: 'device',
    DimensionKind.BROWSER: 'browser',
}


def day_start(day: date) -> datetime:
    """Midnight UTC at the start of `day`."""
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def stat_aggregates() -> dict[str, Count]:
    """The per-day totals as annotations over a `ClickEvent` queryset (shared with `stats`,
    which reads the not-yet-rolled-up days from the events with the same arithmetic)."""
    return {
        'clicks': Count('id', filter=Q(is_bot=False)),
        'bot_clicks': Count('id', filter=Q(is_bot=True)),
        'uniques': Count('ip_hash', distinct=True, filter=Q(is_bot=False)),
        'uniques_with_bots': Count('ip_hash', distinct=True),
    }


def rolled_up_through() -> date | None:
    """The newest day with rollup rows: every complete day up to it has been rolled up."""
    return DailyLinkStat.objects.aggregate(day=Max('date'))['day']


def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _top_values(rows: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Keep a day's `MAX_VALUES_PER_KIND` biggest `(value, count, bot_count)` rows and fold the
    rest into `(other)`."""
    rows = sorted(rows, key=lambda row: (-(row[1] + row[2]), row[0]))
    if len(rows) <= MAX_VALUES_PER_KIND:
        return rows
    kept = rows[: MAX_VALUES_PER_KIND - 1]
    rest = rows[MAX_VALUES_PER_KIND - 1 :]
    kept.append((DailyLinkDimension.OTHER, sum(r[1] for r in rest), sum(r[2] for r in rest)))
    return kept


def rollup_day(day: date) -> int:
    """(Re)write the rollup rows of one UTC day from its events. Returns how many links had
    events. A link with no events that day gets no row (a missing day reads as zero)."""
    events = ClickEvent.objects.filter(at__gte=day_start(day), at__lt=day_start(day + timedelta(1)))
    stats = list(events.order_by().values('link_id').annotate(**stat_aggregates()))
    link_ids = [row['link_id'] for row in stats]

    dimension_rows: list[DailyLinkDimension] = []
    for kind, field in DIMENSION_FIELDS.items():
        grouped: dict[object, list[tuple[str, int, int]]] = defaultdict(list)
        for row in (
            events.order_by()
            .values('link_id', field)
            .annotate(
                n=Count('id', filter=Q(is_bot=False)), bots=Count('id', filter=Q(is_bot=True))
            )
        ):
            grouped[row['link_id']].append((row[field], row['n'], row['bots']))
        for link_id, rows in grouped.items():
            for value, count, bot_count in _top_values(rows):
                dimension_rows.append(
                    DailyLinkDimension(
                        link_id=link_id,
                        date=day,
                        kind=kind,
                        value=value,
                        count=count,
                        bot_count=bot_count,
                    )
                )

    with transaction.atomic():
        for chunk in _chunks(link_ids, _DELETE_CHUNK):
            DailyLinkStat.objects.filter(date=day, link_id__in=chunk).delete()
            DailyLinkDimension.objects.filter(date=day, link_id__in=chunk).delete()
        DailyLinkStat.objects.bulk_create(DailyLinkStat(date=day, **row) for row in stats)
        DailyLinkDimension.objects.bulk_create(dimension_rows)
    return len(link_ids)


def rollup_clicks(now: datetime | None = None) -> int:
    """Roll up every complete UTC day that has events and isn't safely rolled up yet (see the
    module docstring). Returns the number of days written."""
    now = now or dj_timezone.now()
    today_start = day_start(now.astimezone(timezone.utc).date())
    events = ClickEvent.objects.filter(at__lt=today_start)
    watermark = rolled_up_through()
    if watermark is not None:
        events = events.filter(at__gte=day_start(watermark))
    days = (
        events.annotate(day=TruncDate('at', tzinfo=timezone.utc))
        .order_by('day')
        .values_list('day', flat=True)
        .distinct()
    )
    written = 0
    for day in list(days):
        rollup_day(day)
        written += 1
    return written


def purge_click_events(now: datetime | None = None) -> int:
    """Delete the events of whole UTC days older than the retention that are already rolled up,
    in batches. Returns how many were deleted (at most `PURGE_BATCH * PURGE_MAX_BATCHES` a run)."""
    now = now or dj_timezone.now()
    retention = ShortUrlSettings.load().click_event_retention_days
    cutoff = day_start((now - timedelta(days=retention)).astimezone(timezone.utc).date())
    watermark = rolled_up_through()
    if watermark is None:
        return 0  # nothing is rolled up, so nothing may go
    cutoff = min(cutoff, day_start(watermark + timedelta(1)))

    deleted = 0
    for _ in range(PURGE_MAX_BATCHES):
        ids = list(
            ClickEvent.objects.filter(at__lt=cutoff)
            .order_by('pk')
            .values_list('pk', flat=True)[:PURGE_BATCH]
        )
        if not ids:
            break
        ClickEvent.objects.filter(pk__in=ids).delete()
        deleted += len(ids)
    return deleted
