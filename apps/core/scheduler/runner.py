"""Claims and runs due jobs.

The claim is a single conditional `UPDATE`, guarded by the database's own clock (`Now()`) rather
than the calling process's, so two runner processes with clocks that disagree can't both believe
they hold the lease. At-least-once: a job can run twice if a process dies mid-run, so every
`JobSpec.func` must be idempotent.
"""

import logging
import threading
import traceback

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
    """Attempt to claim `job` for this process. Returns whether the claim succeeded.

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
    return updated == 1


def _run_job(job: JobSpec) -> None:
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


def run_due_jobs() -> list[str]:
    """Claim and run every due job. Returns the names of the jobs that ran."""
    ran = []
    for job in get_jobs():
        _ensure_row(job)
        if try_claim(job):
            ran.append(job.name)
            _run_job(job)
    return ran


class SchedulerRunner:
    """Ticks every `tick_seconds`, running whatever jobs are due. Runs until `stop()` is called."""

    def __init__(self, tick_seconds: float, stop_event: threading.Event | None = None):
        self.tick_seconds = tick_seconds
        self._stop_event = stop_event or threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run_forever(self) -> None:
        logger.info(
            'Scheduler runner starting, tick=%ss, jobs=%s',
            self.tick_seconds,
            [job.name for job in get_jobs()],
        )
        while not self._stop_event.is_set():
            # A long-lived thread outside the request cycle would otherwise hold one connection
            # forever and never recover from a database restart; with CONN_MAX_AGE=0 this opens a
            # fresh connection per tick.
            close_old_connections()
            try:
                ran = run_due_jobs()
                if ran:
                    logger.info('Scheduler ran: %s', ran)
            except Exception:
                logger.exception('Scheduler tick failed')
            self._stop_event.wait(self.tick_seconds)
        connections.close_all()
