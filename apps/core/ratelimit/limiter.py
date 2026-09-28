"""Fixed-window rate limiting (issue #53): every protected view across the site (accounts,
qr_code, file_transfer) calls into this one small engine, so there is exactly one place that
decides how counting, keys, storage and the disable switch work.

Design (see CLAUDE.md for the write-up of why a hand-rolled engine over a library):

- **Fixed window, time-bucketed key.** A hit at time `t` for `window_seconds` `w` falls into
  bucket `floor(t / w)`; the storage key embeds the bucket index, and (on the cache-backed path)
  the cache entry's own TTL is set to exactly the time left until that bucket ends. This means a
  bucket's count is *never* read back after its window has passed (the key has expired, or the
  row has been purged -- see below), so there's no separate "reset" step to get wrong, and
  `retry_after` is always the exact time until the *current* bucket ends -- not an approximation
  of some sliding window. The well-known trade-off of any fixed window applies: a burst just
  before a boundary plus another just after can total up to `2 * limit` within `w` seconds of
  each other. Acceptable here -- every limit this project sets is headroom against abuse, not a
  precise quota (see each rule's own comment in `config/settings.py`).
- **Two storage backends, picked by `settings.RATELIMIT_STORAGE`** (`'db'` when no `CACHE_URL` is
  configured -- dev/prod's default and every pytest run, unless a test opts into `'cache'` with
  `override_settings` -- `'cache'` when `CACHE_URL` points at Redis):

  - `'db'`: `apps.core.models.RateLimitCounter`, one row per bucket, incremented with a single
    atomic upsert (`INSERT ... ON CONFLICT ("key") DO UPDATE SET "count" = "count" + 1 RETURNING
    "count"`, raw SQL through `connection.cursor()` -- both Postgres and SQLite >= 3.35 support
    `RETURNING` on an upsert, and Django's cursor wrapper translates the `%s` placeholders used
    below to whichever paramstyle the backend actually needs). This is what replaced the original
    `DatabaseCache`-backed design (see `RateLimitCounter`'s own docstring for the two bugs that
    had: `incr` silently shortening any window over 5 minutes, and `MAX_ENTRIES` culling live
    counters under normal traffic). A purge job (`apps.core.jobs.purge_expired_rate_limit_counters`,
    registered in `CoreConfig.ready()`) deletes rows whose window has ended -- the DB equivalent of
    a cache entry's own TTL expiring, since nothing else here ever deletes a row.
  - `'cache'`: `cache.add` (seed) then `cache.incr` (every later hit in the bucket) against the
    `'ratelimit'` cache alias. Correct on both `RedisCache` (`INCR` is one atomic command that
    preserves the key's existing TTL) and `LocMemCache` (its `incr` mutates the pickled value
    in place without touching `_expire_info`, so it *also* preserves the original TTL) -- the
    combination this project actually ships (Redis in production when `CACHE_URL` is set, LocMem
    under pytest when a test opts into this path). `DatabaseCache` was deliberately never on this
    list: its `incr` doesn't preserve TTL at all (see above), which is exactly why the DB-backed
    path above exists instead of trying to make `DatabaseCache` behave.
- **Keys are always hashed** (`_hash_key`, HMAC-SHA256 keyed with `SECRET_KEY`, matching
  `apps.core.services.email_verification`'s own digest -- both need this because both key on
  emails) before they ever reach storage: a raw email address can exceed a `varchar` column limit
  (silently defeating the limit on `DatabaseCache`, and would need active truncation-collision
  handling here), and storing one in plaintext in a cache/table that isn't itself access-audited
  the way the application's own data is would be a needless PII leak. The hash folds in the rule
  name and the window's bucket index too, not just the caller's raw key, so two different rules
  (or two different windows of the same rule) never collide on the same storage row even though
  they share one namespace (one cache alias, or one DB table).
- **Fails open on a storage error.** A `RedisCache` outage, or any other unexpected exception from
  the storage layer, is caught, logged (`logger.exception`, so it pages/alerts like any other
  unhandled error would) and treated as `RateLimitResult.unlimited()` -- explicit policy: every
  limit here is headroom against abuse, and letting requests through unmetered during an outage is
  a far smaller problem than turning a storage blip into a site-wide 500 on every rate-limited
  view (login, signup, every file_transfer surface, ...).
- **Every over-limit result is logged** (`logger.info`, rule name only -- never the caller's key,
  which may be an email or otherwise identify someone) so 429 rates per rule are visible in
  ordinary log monitoring without needing to instrument every call site separately.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings
from django.core.cache import caches
from django.db import connection

logger = logging.getLogger(__name__)

#: Cache alias rate limit counters use on the `'cache'` storage path (CLAUDE.md: "a dedicated
#: cache alias so rate limiting can be pointed elsewhere later" -- e.g. its own Redis instance,
#: without touching `'default'`). Unused entirely on the `'db'` path (dev/prod's default).
CACHE_ALIAS = 'ratelimit'


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """The outcome of one `hit()`/`peek()` call.

    `remaining` and `retry_after` are best-effort/informational (e.g. for a `Retry-After` header
    or a friendly "try again in Ns" message) -- only `allowed` is load-bearing.
    """

    allowed: bool
    remaining: int
    retry_after: int

    @classmethod
    def unlimited(cls) -> 'RateLimitResult':
        """Always-allowed result, for when there's nothing to count against (rate limiting is
        disabled, no key could be determined -- see `apps.core.ratelimit.keys` -- or the storage
        layer itself failed, see the module docstring's "fails open" note)."""
        return cls(allowed=True, remaining=-1, retry_after=0)


def _hash_key(rule: str, key: str, window_index: int) -> str:
    """Fold `rule`, the caller's raw key and the bucket index into one opaque, fixed-length,
    HMAC-SHA256 storage key -- see the module docstring's "keys are always hashed" note."""
    raw = f'{rule}:{key}:{window_index}'
    return hmac.new(settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _bucket(rule: str, now: float | None) -> tuple[int, int, int, int]:
    """Return `(limit, window_index, retry_after, window_end_epoch)` for `rule` at `now` (or the
    current wall clock when `now` is `None`)."""
    limit, window_seconds = settings.RATELIMIT_RULES[rule]
    t = time.time() if now is None else now
    window_index = int(t // window_seconds)
    window_end = (window_index + 1) * window_seconds
    retry_after = max(1, math.ceil(window_end - t))
    return limit, window_index, retry_after, window_end


def _cache_increment(storage_key: str, timeout: int) -> int:
    cache = caches[CACHE_ALIAS]
    if cache.add(storage_key, 1, timeout=timeout):
        return 1
    try:
        return cache.incr(storage_key)
    except ValueError:
        # The key expired between our `add` (which saw it already present) and this `incr`
        # (window boundary race, or the backend evicted it early) -- reseed rather than fail.
        cache.set(storage_key, 1, timeout=timeout)
        return 1


def _cache_peek(storage_key: str) -> int:
    cache = caches[CACHE_ALIAS]
    value = cache.get(storage_key)
    return 0 if value is None else value


def _ratelimit_counter_table() -> str:
    """`RateLimitCounter._meta.db_table`, isolated in its own function: `_meta` only exists via
    Django's `ModelBase` metaclass, which `ty` (no django-stubs-equivalent plugin here) can't see
    -- see CLAUDE.md's Conventions section on this exact kind of attribute."""
    from ..models import RateLimitCounter  # lazy: avoids a module-load-time model import

    return RateLimitCounter._meta.db_table  # ty: ignore[unresolved-attribute]


def _db_increment(storage_key: str, window_end_epoch: int) -> int:
    qn = connection.ops.quote_name
    table = qn(_ratelimit_counter_table())
    key_col = qn('key')
    count_col = qn('count')
    expires_col = qn('expires_at')
    # `{count_col} + 1` (unqualified) on the right-hand side is ambiguous on Postgres --
    # confirmed against a real Postgres instance while building this: it raises
    # `column reference "count" is ambiguous` inside `ON CONFLICT ... DO UPDATE SET`, since both
    # the conflicting target row and (implicitly) `excluded` are in scope there. Qualifying with
    # the table name (not `excluded`, which would read the just-attempted *insert* value, always
    # `1`) picks the existing row's value, same as SQLite happily accepts either way.
    sql = (
        f'INSERT INTO {table} ({key_col}, {count_col}, {expires_col}) VALUES (%s, 1, %s) '
        f'ON CONFLICT ({key_col}) DO UPDATE SET {count_col} = {table}.{count_col} + 1 '
        f'RETURNING {count_col}'
    )
    window_end = datetime.fromtimestamp(window_end_epoch, tz=UTC)
    with connection.cursor() as cursor:
        cursor.execute(sql, [storage_key, window_end])
        row = cursor.fetchone()
    assert row is not None
    return row[0]


def _db_peek(storage_key: str, now: float) -> int:
    qn = connection.ops.quote_name
    table = qn(_ratelimit_counter_table())
    key_col = qn('key')
    count_col = qn('count')
    expires_col = qn('expires_at')
    sql = f'SELECT {count_col} FROM {table} WHERE {key_col} = %s AND {expires_col} > %s'
    with connection.cursor() as cursor:
        cursor.execute(sql, [storage_key, datetime.fromtimestamp(now, tz=UTC)])
        row = cursor.fetchone()
    return 0 if row is None else row[0]


def _increment(storage_key: str, *, timeout: int, window_end_epoch: int) -> int:
    if settings.RATELIMIT_STORAGE == 'cache':
        return _cache_increment(storage_key, timeout)
    return _db_increment(storage_key, window_end_epoch)


def _current_count(storage_key: str, *, now: float) -> int:
    if settings.RATELIMIT_STORAGE == 'cache':
        return _cache_peek(storage_key)
    return _db_peek(storage_key, now)


def hit(key: str, rule: str, *, now: float | None = None) -> RateLimitResult:
    """Count one hit against `key` under `rule` (a name in `settings.RATELIMIT_RULES`), and
    report whether this hit is still within the rule's `(limit, window_seconds)`.

    Does nothing (always allowed) when `settings.RATELIMIT_ENABLE` is `False` -- the project-wide
    kill switch, off by default under pytest so unrelated tests never become flaky from
    incidentally tripping a limit (see `config/settings.py`).

    `now` is for tests only: pinning the bucket boundary deterministically instead of depending on
    wall-clock time crossing it mid-test.
    """
    if not settings.RATELIMIT_ENABLE:
        return RateLimitResult.unlimited()

    limit, window_index, retry_after, window_end = _bucket(rule, now)
    storage_key = _hash_key(rule, key, window_index)

    try:
        count = _increment(storage_key, timeout=retry_after, window_end_epoch=window_end)
    except Exception:
        logger.exception('Rate limit storage error on rule %s; failing open', rule)
        return RateLimitResult.unlimited()

    result = RateLimitResult(
        allowed=count <= limit, remaining=max(0, limit - count), retry_after=retry_after
    )
    if not result.allowed:
        logger.info('Rate limit exceeded: rule=%s', rule)
    return result


def peek(key: str, rule: str, *, now: float | None = None) -> RateLimitResult:
    """Report whether `key` is currently within `rule`'s budget *without* counting this call as a
    hit -- for a caller that needs to decide whether to even attempt a sensitive operation (e.g.
    `authenticate()`) before knowing whether it will succeed, and only wants to *record* a hit
    once that operation's own outcome is known (see `apps.accounts.services.login_throttle` for
    the caller that needs exactly this).

    Note the boundary is one off from `hit()`'s: `hit()` allows a bucket's *`limit`th* hit (the
    count *after* incrementing is compared with `<=`), since by the time it's called the hit has
    already happened and must be counted regardless. `peek()` instead compares the count *so far*
    (nothing pending) with `<`, since it's asking "should the caller even attempt the thing that
    might add one more" -- once `limit` hits have already been recorded, a `peek()` must deny the
    *next* one before it's attempted, not wait for it to be attempted and only then discover it
    was one too many.

    Same kill switch and `now` behaviour as `hit()`.
    """
    if not settings.RATELIMIT_ENABLE:
        return RateLimitResult.unlimited()

    limit, window_index, retry_after, _window_end = _bucket(rule, now)
    storage_key = _hash_key(rule, key, window_index)
    t = time.time() if now is None else now

    try:
        count = _current_count(storage_key, now=t)
    except Exception:
        logger.exception('Rate limit storage error on rule %s; failing open', rule)
        return RateLimitResult.unlimited()

    result = RateLimitResult(
        allowed=count < limit, remaining=max(0, limit - count), retry_after=retry_after
    )
    if not result.allowed:
        logger.info('Rate limit exceeded: rule=%s', rule)
    return result


def purge_expired(now: float | None = None) -> int:
    """Delete every DB-backed counter row whose window has already ended. A no-op when
    `settings.RATELIMIT_STORAGE` isn't `'db'` (the cache-backed path expires its own entries via
    the cache's TTL, same as it always has). Used by `apps.core.jobs.purge_expired_rate_limit_counters`,
    the periodic job that stands in for the "reset" a `DatabaseCache` counter used to get for free
    from `cache.add`'s own eventual expiry.
    """
    if settings.RATELIMIT_STORAGE != 'db':
        return 0

    from ..models import RateLimitCounter  # lazy: avoids a module-load-time model import

    cutoff = datetime.fromtimestamp(time.time() if now is None else now, tz=UTC)
    deleted, _ = RateLimitCounter.objects.filter(expires_at__lt=cutoff).delete()
    return deleted
