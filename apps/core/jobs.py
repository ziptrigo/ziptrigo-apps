"""`core`'s own periodic jobs: purging old `EmailVerification` rows (issue #58), and purging
expired `RateLimitCounter` rows (issue #53 code review). Registered from `CoreConfig.ready()`, the
same way each product registers its own jobs with `apps.core.scheduler` -- see CLAUDE.md, "Queue
and scheduler". `core` has no product to belong to, so it registers these on itself.
"""

import logging

from .ratelimit import purge_expired
from .services.email_verification import purge_old

logger = logging.getLogger(__name__)


def purge_old_email_verifications() -> None:
    """Daily: delete `EmailVerification` rows older than 30 days (`purge_old`'s default)."""
    try:
        deleted = purge_old()
    except Exception:
        logger.exception('purge_old_email_verifications failed')
        return
    if deleted:
        logger.info('purge_old_email_verifications deleted %d row(s)', deleted)


def purge_expired_rate_limit_counters() -> None:
    """Hourly: delete `RateLimitCounter` rows whose fixed window has already ended -- the DB
    storage path's equivalent of a cache entry's own TTL expiring (`apps.core.ratelimit.purge_expired`
    is a no-op on the cache storage path, where nothing needs this job at all)."""
    try:
        deleted = purge_expired()
    except Exception:
        logger.exception('purge_expired_rate_limit_counters failed')
        return
    if deleted:
        logger.info('purge_expired_rate_limit_counters deleted %d row(s)', deleted)
