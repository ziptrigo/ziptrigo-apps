"""Claims and runs due jobs.

The claim is a single conditional `UPDATE`, guarded by the database's own clock (`Now()`) rather
than the calling process's, so two runner processes with clocks that disagree can't both believe
they hold the lease. At-least-once: a job can run twice if a process dies mid-run, so every
`JobSpec.func` must be idempotent.
"""

import logging
import threading
import traceback
from datetime import datetime, timedelta

from django.db import close_old_connections, connections
from django.db.models import DateTimeField, ExpressionWrapper, F, Q
from django.db.models.functions import Now

from ..models import ScheduledJob
from .registry import JobSpec, get_jobs

logger = logging.getLogger(__name__)


def _ensure_row(job: JobSpec) -> None:
    ScheduledJob.objects.get_or_create(
        name=job.name,
        defaults={'interval_seconds': int(job.interval.total_seconds())},
    )


def try_claim(job: JobSpec) -> bool:
    """Attempt to claim `job` for this process. Returns whether the claim succeeded (see
    `_claim`)."""
    return _claim(job) is not None


def _claim(job: JobSpec) -> datetime | None:
    """Claim `job`, returning the claim's `last_started_at` (the token `_run_job` releases the
    lease with), or `None` when it isn't claimable.

    Succeeds only when the row's lease isn't currently held (`locked_until` unset or in the past,
    per the database clock) and the job is due (`last_started_at` unset or older than its
    interval, also per the database clock -- using the calling process's own clock here instead
    would let two runners whose clocks disagree, even slightly, disagree about whether the job is
    due). Claiming sets `locked_until` to now + the job's lease and `last_started_at` to now.
    """
    due_before = ExpressionWrapper(Now() - job.interval, output_field=DateTimeField())
    lease_until = ExpressionWrapper(Now() + job.lease, output_field=DateTimeField())

    updated = (
        ScheduledJob.objects.filter(name=job.name)
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lt=Now()))
        .filter(Q(last_started_at__isnull=True) | Q(last_started_at__lte=due_before))
        .update(
            locked_until=lease_until,
            last_started_at=Now(),
            interval_seconds=int(job.interval.total_seconds()),
        )
    )
    if updated != 1:
        return None
    # Read back the database's own timestamp: no other runner can claim the row while this
    # claim's lease holds, so this is the value this claim set.
    return (
        ScheduledJob.objects.filter(name=job.name).values_list('last_started_at', flat=True).first()
    )


#: Slack for the release comparison in `_run_job`; a real re-claim comes a whole lease later.
_CLAIM_TOLERANCE = timedelta(milliseconds=1)


def _run_job(job: JobSpec, claimed_at: datetime | None) -> None:
    """Run `job` and record the outcome, then release the lease, so the job is due again once its
    interval passes rather than once its (usually longer) lease runs out. The release only
    applies while no later claim exists (`last_started_at` not after `claimed_at`): a run that
    outlived its lease and was claimed again by another runner must not free that runner's lease.
    The test is `< claimed_at + _CLAIM_TOLERANCE`, not `==` or `<=`: SQLite stores `Now()` as text
    with millisecond precision (`...:00.123`) but compares it with the read-back value rendered
    without a fraction when it is a whole second (`...:00`), and a longer string sorts after its
    prefix, so `<=` missed the claim 1 time in 1000. A process that dies mid-job never gets here, so its lease
    holds until it expires."""
    try:
        job.func()
    except Exception:
        logger.exception('Scheduled job %s failed', job.name)
        ScheduledJob.objects.filter(name=job.name).update(
            last_finished_at=Now(),
            last_error=traceback.format_exc(),
        )
    else:
        ScheduledJob.objects.filter(name=job.name).update(
            last_finished_at=Now(),
            last_success_at=Now(),
            last_error='',
            run_count=F('run_count') + 1,
        )
    finally:
        if claimed_at is not None:
            ScheduledJob.objects.filter(
                name=job.name, last_started_at__lt=claimed_at + _CLAIM_TOLERANCE
            ).update(locked_until=None)


def run_due_jobs() -> list[str]:
    """Claim and run every due job. Returns the names of the jobs that ran."""
    ran = []
    for job in get_jobs():
        _ensure_row(job)
        claimed_at = _claim(job)
        if claimed_at is not None:
            ran.append(job.name)
            _run_job(job, claimed_at)
    return ran


class SchedulerRunner:
    """Ticks every `tick_seconds`, running whatever jobs are due. Runs until `stop()` is called."""

    def __init__(self, tick_seconds: float, stop_event: threading.Event | None = None):
        self.tick_seconds = tick_seconds
        self._stop_event = stop_event or threading.Event()
        # Set by `start_scheduler_thread()` so the owner can join it after `stop()`.
        self.thread: threading.Thread | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def run_forever(self) -> None:
        logger.info(
            'Scheduler runner starting, tick=%ss, jobs=%s',
            self.tick_seconds,
            [job.name for job in get_jobs()],
        )
        while not self._stop_event.is_set():
            # A long-lived thread outside the request cycle never gets the request-cycle cleanup.
            # With `DATABASE_URL` (prod/staging) the connection is persistent (conn_max_age=600,
            # conn_health_checks), so this thread keeps one health-checked connection (one extra
            # Postgres connection per gunicorn worker); the check here is what recovers it after a
            # database restart. With no max age (SQLite, dev) it opens a fresh one per tick.
            close_old_connections()
            try:
                ran = run_due_jobs()
                if ran:
                    logger.info('Scheduler ran: %s', ran)
            except Exception:
                logger.exception('Scheduler tick failed')
            self._stop_event.wait(self.tick_seconds)
        connections.close_all()
