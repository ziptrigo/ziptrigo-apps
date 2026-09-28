"""The job registry: app-side of the scheduler.

Each app that wants a periodic job registers a `JobSpec` from its own `AppConfig.ready()`, the
same way products register themselves with `apps.core.products`. `core` never imports the app
that owns a job -- it only ever calls back into `JobSpec.func`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class JobSpec:
    """A periodic job.

    Attributes:
        name: Unique job name, also the `ScheduledJob` primary key (e.g. `'file_transfer.meter_transfers'`).
        func: Zero-argument callable. Must be idempotent: the scheduler is at-least-once, so the
            same due job can run twice (e.g. if the process is killed after claiming but before
            finishing).
        interval: Minimum time between runs. The job is "due" once this much time has passed since
            it last *started* (not since it finished), so a slow run doesn't get a shorter wait.
        lease: How long a claim is held before another runner is allowed to consider the job
            unclaimed again (protects against a crashed run holding the lease forever). Should be
            comfortably longer than the job is ever expected to take.
    """

    name: str
    func: Callable[[], None]
    interval: timedelta
    lease: timedelta = timedelta(minutes=10)


_registry: dict[str, JobSpec] = {}


def register(job: JobSpec) -> None:
    """Register a job. Registering the same name again replaces the previous entry."""
    _registry[job.name] = job


def get_jobs() -> list[JobSpec]:
    """Return the registered jobs, in registration order."""
    return list(_registry.values())


def clear_registry() -> None:
    """Remove every registered job. Test-only: production always registers the same set from
    `AppConfig.ready()`."""
    _registry.clear()
