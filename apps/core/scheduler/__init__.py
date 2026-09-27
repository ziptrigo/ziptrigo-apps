"""In-process job scheduler, run by the `run_scheduler` management command in the worker
container (never in the web container).

Mirrors `apps.core.products`: `core` can't import a product app, so products register their jobs
from their own `AppConfig.ready()` instead of `core` discovering them. See `apps/core/scheduler/registry.py`
for `JobSpec` / `register`, and `apps/core/scheduler/runner.py` for the claim-and-run loop.
"""

from .registry import JobSpec, clear_registry, get_jobs, register
from .runner import SchedulerRunner, run_due_jobs, try_claim

__all__ = [
    'JobSpec',
    'SchedulerRunner',
    'clear_registry',
    'get_jobs',
    'register',
    'run_due_jobs',
    'try_claim',
]
