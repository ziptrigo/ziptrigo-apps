"""Runs a `SchedulerRunner` on a daemon thread inside the current process.

Safe with several gunicorn workers: `try_claim` is an atomic conditional `UPDATE` guarded by the
database clock, so only one thread wins each job per interval.
"""

import logging
import os
import threading

from django.conf import settings

from .runner import SchedulerRunner

logger = logging.getLogger(__name__)


def start_scheduler_thread() -> SchedulerRunner | None:
    """Start a daemon thread running a SchedulerRunner. No-op (returns None) unless
    settings.SCHEDULER_ENABLED. Returns the runner so the caller can .stop() it and
    `.thread.join()` it."""
    if not settings.SCHEDULER_ENABLED:
        return None
    runner = SchedulerRunner(tick_seconds=settings.SCHEDULER_TICK_SECONDS)
    runner.thread = threading.Thread(target=runner.run_forever, name='scheduler', daemon=True)
    runner.thread.start()
    logger.info('Scheduler thread started in pid %s', os.getpid())
    return runner
