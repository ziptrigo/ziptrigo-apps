"""Schemas for `GET /api/s/links/{id}/stats`."""

from datetime import date

from ninja import Schema


class TotalsSchema(Schema):
    """The numbers every link has, whatever it has paid for. `clicks` and `bot_clicks` are the
    link's own counters (see `LinkSchema`); `uniques` sums the daily unique visitors (someone who
    returns on another day counts again), bots left out while `filter_bots` is on."""

    clicks: int
    bot_clicks: int
    uniques: int
    filter_bots: bool


class BucketSchema(Schema):
    """One point of the time series: a day, a week (starting Monday) or a calendar month."""

    start: date
    label: str
    clicks: int


class TopRowSchema(Schema):
    value: str
    label: str
    count: int
    percent: int


class TopTableSchema(Schema):
    """`kind` is `referrer`, `country`, `device` or `browser`; an empty `value` is "unknown"
    (`label` says so) and `Other` folds the long tail."""

    kind: str
    title: str
    rows: list[TopRowSchema]


class StatsDetailSchema(Schema):
    """The paid part: the range's figures (UTC days, `start` through `end`), the time series and the
    four top lists. `bucket_days` is the width of one bucket: 1, 7, or 0 for a calendar month."""

    start: date
    end: date
    clicks: int
    uniques: int
    bot_clicks: int
    bucket_days: int
    buckets: list[BucketSchema]
    tables: list[TopTableSchema]


class StatsSchema(Schema):
    """A link's statistics. `totals` is always there; `detail` is `null` until the detailed
    analytics are open (`analytics_enabled`; open them with `POST /links/{id}/analytics`, which
    costs `analytics_cost` credits once). Events are recorded from the first click, so opening it
    later shows the whole history."""

    range: str
    totals: TotalsSchema
    analytics_enabled: bool
    analytics_cost: int
    detail: StatsDetailSchema | None
