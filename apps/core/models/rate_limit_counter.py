from datetime import datetime
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class RateLimitCounter(models.Model):
    """One row per fixed-window rate-limit bucket (issue #53 code review): the DB-backed storage
    `apps.core.ratelimit` uses when `settings.RATELIMIT_STORAGE == 'db'` (no `CACHE_URL`
    configured -- dev/prod's default). Replaces the original `DatabaseCache`-backed design, which
    had two bugs: `DatabaseCache.incr` falls back to `BaseCache.incr` (get, then a plain `set` at
    the *default* timeout), silently shortening any window longer than that default the moment a
    second hit lands in the same bucket; and `DatabaseCache`'s `MAX_ENTRIES`/culling deletes rows
    (including other rules' still-live counters) once the table grows past a few hundred rows.

    A row's primary key (`key`) is a keyed hash (`apps.core.ratelimit.limiter._hash_key`) of the
    rule name, the caller's key and the fixed-window bucket index -- never the raw value -- so
    this table can never reveal which account, email address, IP or transfer a bucket belongs to,
    and never overflows a column limit on a long value (e.g. an email address). Because the
    bucket index is baked into the hash, a new window is always a *new* row (a plain `INSERT`),
    never a reset of an old one -- `apps.core.jobs.purge_expired_rate_limit_counters` (issue #53)
    deletes rows whose `expires_at` has passed, the same way an equivalent cache entry would have
    simply expired on its own.

    Incremented with a single atomic upsert (`INSERT ... ON CONFLICT ("key") DO UPDATE SET
    "count" = "count" + 1 RETURNING "count"`, raw SQL via `connection.cursor()` -- see
    `apps.core.ratelimit.limiter._db_increment`), which is what actually fixes the race
    `DatabaseCache.incr`'s get-then-set couldn't close: two concurrent hits in the same bucket can
    never both read the same pre-increment value.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    key = cast(str, models.CharField(max_length=64, primary_key=True))
    count = cast(int, models.PositiveIntegerField(default=0))
    expires_at = cast(datetime, models.DateTimeField(db_index=True))

    class Meta:
        verbose_name = 'Rate limit counter'
        verbose_name_plural = 'Rate limit counters'

    def __str__(self) -> str:
        return f'{self.key} ({self.count})'
