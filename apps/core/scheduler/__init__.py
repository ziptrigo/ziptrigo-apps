"""In-process job scheduler. Two entry points share one `SchedulerRunner`: the `run_scheduler`
management command (standalone, for local development or a dedicated container) and
`start_scheduler_thread()` (a daemon thread inside each gunicorn worker, started from
`gunicorn.conf.py` when `SCHEDULER_ENABLED`; this is how the deployed image runs it).

Mirrors `apps.core.products`: `core` can't import a product app, so products register their jobs
from their own `AppConfig.ready()` instead of `core` discovering them. See `apps/core/scheduler/registry.py`
for `JobSpec` / `register`, and `apps/core/scheduler/runner.py` for the claim-and-run loop.
"""

from .registry import JobSpec, clear_registry, get_jobs, register
from .runner import SchedulerRunner, run_due_jobs, try_claim
from .thread import start_scheduler_thread

__all__ = [
    'JobSpec',
    'SchedulerRunner',
    'clear_registry',
    'get_jobs',
    'register',
    'run_due_jobs',
    'start_scheduler_thread',
    'try_claim',
]
