"""`core`'s own periodic jobs: purging old `EmailVerification` rows (issue #58), purging
expired `RateLimitCounter` rows (issue #53 code review), and pruning finished task results and
expired sessions (audit #18). A job lets its exceptions propagate: the scheduler's runner logs them
and records the run as failed (`ScheduledJob.last_error`), which a swallowed one would hide. Registered from `CoreConfig.ready()`, the
same way each product registers its own jobs with `apps.core.scheduler` -- see CLAUDE.md, "Queue
and scheduler". `core` has no product to belong to, so it registers these on itself.
"""

import logging
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from .ratelimit import purge_expired
from .services.email_verification import purge_old

logger = logging.getLogger(__name__)

#: How long a finished (succeeded or failed) `django_tasks_db` result is kept. Each row carries the
#: task's arguments, return value and traceback, so it holds personal data and grows by thousands a
#: day; two weeks is plenty to look into a failure.
TASK_RESULT_RETENTION = timedelta(days=14)

#: Rows deleted per statement, so one run never holds a long lock on a table that was never pruned.
PURGE_BATCH = 1000


def purge_old_email_verifications() -> None:
    """Daily: delete `EmailVerification` rows older than 30 days (`purge_old`'s default)."""
    deleted = purge_old()
    if deleted:
        logger.info('purge_old_email_verifications deleted %d row(s)', deleted)


def purge_expired_rate_limit_counters() -> None:
    """Hourly: delete `RateLimitCounter` rows whose fixed window has already ended -- the DB
    storage path's equivalent of a cache entry's own TTL expiring (`apps.core.ratelimit.purge_expired`
    is a no-op on the cache storage path, where nothing needs this job at all)."""
    deleted = purge_expired()
    if deleted:
        logger.info('purge_expired_rate_limit_counters deleted %d row(s)', deleted)


def purge_old_task_results() -> None:
    """Daily: delete finished `DBTaskResult` rows older than `TASK_RESULT_RETENTION`, in batches.
    Only `succeeded`/`failed` rows go (`finished()`); `ready`/`running` ones are never touched.
    Idempotent and safe to run twice at once (a row already deleted is simply not matched)."""
    cutoff = timezone.now() - TASK_RESULT_RETENTION
    total = 0
    while True:
        ids = list(
            DBTaskResult.objects.finished()
            .filter(finished_at__lte=cutoff)
            .values_list('pk', flat=True)[:PURGE_BATCH]
        )
        if not ids:
            break
        total += DBTaskResult.objects.filter(pk__in=ids).delete()[0]
    if total:
        logger.info('purge_old_task_results deleted %d row(s)', total)


def clear_expired_sessions() -> None:
    """Daily: delete expired sessions. The database session backend never purges them itself
    (Django leaves that to `clearsessions`); this is that command, for the configured engine."""
    import_module(settings.SESSION_ENGINE).SessionStore.clear_expired()
