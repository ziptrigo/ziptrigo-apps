"""Click analytics (issue #162): one raw event per counted click, rolled up into daily totals.

Raw events (`ClickEvent`) are kept `ShortUrlSettings.click_event_retention_days` (90 by default);
the rollups (`DailyLinkStat`, `DailyLinkDimension`) are kept forever. All days are **UTC dates**
(`TIME_ZONE` is UTC; the rollup, the unique-visitor hash and the stats page all use it).
"""

import datetime as dt
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast

from django.db import models
from django.utils import timezone

from .link import ShortLink

if TYPE_CHECKING:
    from django.db.models import Manager


class Device(models.TextChoices):
    DESKTOP = 'desktop', 'Desktop'
    MOBILE = 'mobile', 'Mobile'
    TABLET = 'tablet', 'Tablet'
    BOT = 'bot', 'Bot'


class DimensionKind(models.TextChoices):
    REFERRER = 'referrer', 'Referrer'
    COUNTRY = 'country', 'Country'
    DEVICE = 'device', 'Device'
    BROWSER = 'browser', 'Browser'


class ClickEvent(models.Model):
    """One counted click. Holds nothing that identifies a visitor: the address is only a keyed
    hash that changes every day (so a visitor can be counted once a day without being stored),
    and the referrer is the host alone."""

    objects: ClassVar['Manager']
    link_id: uuid.UUID

    link = cast(
        ShortLink,
        models.ForeignKey(ShortLink, on_delete=models.CASCADE, related_name='click_events'),
    )
    at = cast(datetime, models.DateTimeField(default=timezone.now))
    #: HMAC-SHA256 hex of the UTC day and the normalized client address
    #: (`services.clicks.visitor_hash`).
    ip_hash = cast(str, models.CharField(max_length=64))
    #: Host of the `Referer` header; empty for a direct visit, an own-site referrer or none.
    referrer_host = cast(str, models.CharField(max_length=255, blank=True))
    #: ISO 3166-1 alpha-2, empty when unknown (no GeoIP database, or an address it doesn't hold).
    country = cast(str, models.CharField(max_length=2, blank=True))
    device = cast(str, models.CharField(max_length=10, choices=Device.choices))
    browser = cast(str, models.CharField(max_length=64, blank=True))
    os = cast(str, models.CharField(max_length=64, blank=True))
    is_bot = cast(bool, models.BooleanField(default=False))

    class Meta:
        indexes = [
            models.Index(fields=['link', 'at'], name='short_click_link_at_idx'),
            # The purge and the rollup read by time alone.
            models.Index(fields=['at'], name='short_click_at_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.link_id} @ {self.at:%Y-%m-%d %H:%M}'


class DailyLinkStat(models.Model):
    """One link's totals for one UTC day, written by `short_url.rollup_clicks`.

    `clicks` and `uniques` are the humans', `bot_clicks` the crawlers' and `uniques_with_bots`
    every distinct visitor of the day, so the stats page can show either view
    (`ShortLink.filter_bots`) from the same row. A visitor is one hashed address of that day, so
    a range's "unique visitors" is the sum of its days: someone back on another day counts again.
    """

    objects: ClassVar['Manager']
    link_id: uuid.UUID

    link = cast(
        ShortLink,
        models.ForeignKey(ShortLink, on_delete=models.CASCADE, related_name='daily_stats'),
    )
    date = cast(dt.date, models.DateField())
    clicks = cast(int, models.PositiveIntegerField(default=0))
    uniques = cast(int, models.PositiveIntegerField(default=0))
    bot_clicks = cast(int, models.PositiveIntegerField(default=0))
    uniques_with_bots = cast(int, models.PositiveIntegerField(default=0))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['link', 'date'], name='short_daily_stat_unique'),
        ]
        indexes = [models.Index(fields=['date'], name='short_daily_stat_date_idx')]
        ordering = ['-date']

    def __str__(self) -> str:
        return f'{self.link_id} {self.date}'


class DailyLinkDimension(models.Model):
    """One link's clicks for one UTC day with one referrer / country / device / browser. `count`
    is the humans', `bot_count` the crawlers'. An empty `value` is "unknown" (a direct visit for
    a referrer), and `OTHER` collects what didn't fit in the day's top values."""

    objects: ClassVar['Manager']

    #: The value that stands for everything beyond a day's `services.rollup.MAX_VALUES_PER_KIND`.
    OTHER: ClassVar[str] = '(other)'

    link_id: uuid.UUID

    link = cast(
        ShortLink,
        models.ForeignKey(ShortLink, on_delete=models.CASCADE, related_name='daily_dimensions'),
    )
    date = cast(dt.date, models.DateField())
    kind = cast(str, models.CharField(max_length=10, choices=DimensionKind.choices))
    value = cast(str, models.CharField(max_length=255, blank=True))
    count = cast(int, models.PositiveIntegerField(default=0))
    bot_count = cast(int, models.PositiveIntegerField(default=0))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['link', 'date', 'kind', 'value'], name='short_daily_dimension_unique'
            ),
        ]
        indexes = [models.Index(fields=['link', 'kind', 'date'], name='short_daily_dim_idx')]

    def __str__(self) -> str:
        return f'{self.link_id} {self.date} {self.kind}={self.value}'
