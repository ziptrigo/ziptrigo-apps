"""Fixed-window rate limiting on a dedicated cache (issue #53): every protected view across the
site (accounts, qr_code, file_transfer) calls into this one small engine, so there is exactly one
place that decides how counting, keys and the disable switch work.

Design (see CLAUDE.md for the write-up of why a cache over a library):

- **Fixed window, time-bucketed key.** A hit at time `t` for `window_seconds` `w` falls into
  bucket `floor(t / w)`; the cache key embeds the bucket index, and the cache entry's own TTL is
  set to exactly the time left until that bucket ends. This means a bucket's count is *never*
  read back after its window has passed (the key has expired), so there's no separate "reset"
  step to get wrong, and `retry_after` is always the exact time until the *current* bucket ends
  -- not an approximation of some sliding window. The well-known trade-off of any fixed window
  applies: a burst just before a boundary plus another just after can total up to `2 * limit`
  within `w` seconds of each other. Acceptable here -- every limit this project sets is headroom
  against abuse, not a precise quota (see each rule's own comment in `config/settings.py`).
- **Counting via `cache.add` + `cache.incr`.** The first hit in a bucket seeds the counter with
  `add` (only succeeds if the key doesn't exist yet); every later hit in the same bucket
  increments it. Both are atomic per-key operations on every backend this project uses for the
  `'ratelimit'` cache alias (`LocMemCache` under pytest guards its dict with a lock;
  `RedisCache`'s `INCR` is a single atomic command). The one exception is `DatabaseCache`
  (this project's non-Redis default, see `config/settings.py`): its `incr` does a `SELECT` then an
  `UPDATE` inside a transaction, which is not perfectly safe against two requests reading the
  same pre-increment value under Postgres's default `READ COMMITTED` isolation. That's a
  deliberate, accepted gap: a lost increment only ever *undercounts*, which means letting a
  handful of extra requests through a limit that's already generous headroom -- never the
  reverse (never locking someone out early). A precise quota would need `select_for_update` or a
  single atomic `UPDATE ... SET value = value + 1`, which `DatabaseCache` doesn't offer; not
  worth hand-rolling for this.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import caches

#: Cache alias every rate limit counter is stored under (CLAUDE.md: "a dedicated cache alias so
#: rate limiting can be pointed elsewhere later" -- e.g. its own Redis instance, without touching
#: `'default'`).
CACHE_ALIAS = 'ratelimit'


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """The outcome of one `hit()` call.

    `remaining` and `retry_after` are best-effort/informational (e.g. for a `Retry-After` header
    or a friendly "try again in Ns" message) -- only `allowed` is load-bearing.
    """

    allowed: bool
    remaining: int
    retry_after: int

    @classmethod
    def unlimited(cls) -> 'RateLimitResult':
        """Always-allowed result, for when there's nothing to count against (rate limiting is
        disabled, or the caller couldn't determine a key -- see `apps.core.ratelimit.keys`)."""
        return cls(allowed=True, remaining=-1, retry_after=0)


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

    limit, window_seconds = settings.RATELIMIT_RULES[rule]
    now = time.time() if now is None else now
    window_index = int(now // window_seconds)
    window_end = (window_index + 1) * window_seconds
    retry_after = max(1, math.ceil(window_end - now))
    cache_key = f'ratelimit:{rule}:{key}:{window_index}'

    cache = caches[CACHE_ALIAS]
    if cache.add(cache_key, 1, timeout=retry_after):
        count = 1
    else:
        try:
            count = cache.incr(cache_key)
        except ValueError:
            # The key expired between our `add` (which saw it already present) and this `incr`
            # (window boundary race, or the backend evicted it early) -- reseed rather than fail.
            cache.set(cache_key, 1, timeout=retry_after)
            count = 1

    return RateLimitResult(
        allowed=count <= limit, remaining=max(0, limit - count), retry_after=retry_after
    )
