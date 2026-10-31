"""Destination reputation (issue #163): Google Safe Browsing applied to short links.

The client is `services.safe_browsing`; this is the policy. Three places use it:

- **Create and destination edit** (`check_destination`, called by `links._prepare`, `update_link`
  and the dry runs `check_new_link` / `check_update`): a flagged destination is refused with
  `UnsafeDestinationError`, the same neutral message as a blocked host. Nothing is looked up for
  an unchanged destination, and a verdict is remembered for `CACHE_TTL` so the dry run and the
  real call of one request (and a retry) make one lookup, not two.
- **The daily recheck** (`recheck`, the `short_url.recheck_destinations` job): active links whose
  destination was last found clean more than `RECHECK_INTERVAL` ago, oldest first, `RECHECK_BATCH`
  distinct destinations a run (one API request). A flagged one puts its links on the abuse-review
  hold (`hold.hold_for_threat`) with a pending report as the reason, for staff to take down or
  release. Releasing it (or dismissing that report) clears the destination
  (`ShortLink.reputation_cleared_destination`), so the recheck doesn't hold it again until the
  owner changes it.
- **Not at redirect time**: a link flagged since its last check keeps redirecting until the next
  recheck (at most a day, plus the run's queue), which keeps the hottest path free of a call.

Everything **fails open**: not configured, a timeout, quota or a bad reply never refuses a create
or edit and never holds a link; the link just isn't marked as checked, so the recheck looks again.
The recheck lets the failure propagate, so `ScheduledJob.last_error` shows it.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import F, Q
from django.utils import timezone

from ..models import LinkStatus, ShortLink
from . import hold, safe_browsing
from .errors import UnsafeDestinationError
from .safe_browsing import SafeBrowsingUnavailable

logger = logging.getLogger(__name__)

#: How long a lookup's answer is reused (seconds), and how many answers are kept.
CACHE_TTL = 600
CACHE_MAX = 2000
#: A link whose destination was found clean longer ago than this is looked at again.
RECHECK_INTERVAL = timedelta(hours=24)
#: Distinct destinations per recheck run: one API request.
RECHECK_BATCH = safe_browsing.MAX_URLS_PER_REQUEST
#: Rows read to fill a batch, so a few destinations shared by thousands of links can't make the
#: query read them all.
RECHECK_SCAN_LIMIT = RECHECK_BATCH * 10

_lock = threading.Lock()
#: url -> (monotonic expiry, threat type or '' for clean)
_cache: dict[str, tuple[float, str]] = {}


def reset_cache() -> None:
    """Forget every remembered verdict (tests)."""
    with _lock:
        _cache.clear()


def _cached(url: str) -> str | None:
    with _lock:
        entry = _cache.get(url)
        if entry is None:
            return None
        if entry[0] <= time.monotonic():
            del _cache[url]
            return None
        return entry[1]


def _remember(verdicts: dict[str, str]) -> None:
    expires = time.monotonic() + CACHE_TTL
    with _lock:
        if len(_cache) + len(verdicts) > CACHE_MAX:
            _cache.clear()
        for url, threat in verdicts.items():
            _cache[url] = (expires, threat)


def is_enabled() -> bool:
    return safe_browsing.is_configured()


def check_destination(url: str) -> bool:
    """Refuse `url` when Safe Browsing flags it (`UnsafeDestinationError`). Returns True when it
    was found clean, False when it could not be checked (off, or the API failed: fail open)."""
    if not is_enabled():
        return False
    threat = _cached(url)
    if threat is None:
        try:
            flagged = safe_browsing.get_client().lookup([url])
        except SafeBrowsingUnavailable as exc:
            logger.warning('Safe Browsing lookup failed (%s); allowing the destination.', exc)
            return False
        threat = flagged.get(url, '')
        _remember({url: threat})
    if threat:
        logger.info('Safe Browsing refused a destination (%s).', threat)
        raise UnsafeDestinationError()
    return True


@dataclass(frozen=True, slots=True)
class RecheckResult:
    checked: int = 0
    held: int = 0


def _due_destinations(now: datetime) -> list[str]:
    due = (
        ShortLink.objects.filter(status=LinkStatus.ACTIVE, held_for_review_at__isnull=True)
        .filter(
            Q(reputation_checked_at__isnull=True)
            | Q(reputation_checked_at__lt=now - RECHECK_INTERVAL)
        )
        .exclude(destination=F('reputation_cleared_destination'))
        .order_by(F('reputation_checked_at').asc(nulls_first=True), 'created_at')
        .values_list('destination', flat=True)[:RECHECK_SCAN_LIMIT]
    )
    # dict, not set: keeps the oldest-first order while dropping repeats.
    return list(dict.fromkeys(due))[:RECHECK_BATCH]


def recheck(*, now: datetime | None = None) -> RecheckResult:
    """Look at the destinations of the active links that are due, hold the ones that are now
    flagged and mark the rest as checked. Does nothing when Safe Browsing is not configured.
    Raises `SafeBrowsingUnavailable` when the API can't answer, with nothing marked as checked,
    so the next run takes the same links again."""
    if not is_enabled():
        return RecheckResult()
    now = now or timezone.now()
    urls = _due_destinations(now)
    if not urls:
        return RecheckResult()

    flagged = safe_browsing.get_client().lookup(urls)

    clean = [url for url in urls if url not in flagged]
    if clean:
        ShortLink.objects.filter(destination__in=clean, status=LinkStatus.ACTIVE).update(
            reputation_checked_at=now
        )
    held = 0
    for url in urls:
        threat = flagged.get(url)
        if not threat:
            continue
        links = ShortLink.objects.filter(
            destination=url, status=LinkStatus.ACTIVE, held_for_review_at__isnull=True
        ).exclude(reputation_cleared_destination=url)
        for link in links:
            held += hold.hold_for_threat(link, url, threat)
    _remember({url: flagged.get(url, '') for url in urls})
    return RecheckResult(checked=len(urls), held=held)
