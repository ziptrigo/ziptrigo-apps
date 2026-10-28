"""The jobs the To-do app registers with `apps.core.scheduler` (wired up in `apps.py`).

Idempotent (the scheduler is at-least-once). A row that can't be processed is logged and skipped by
the service itself, so one bad task can't hold up the rest of the run.
"""

import logging

from .services import tasks

logger = logging.getLogger(__name__)


def purge_trash() -> None:
    """Daily: delete the tasks trashed longer than `TodoSettings.trash_days` ago."""
    deleted = tasks.purge_trash()
    if deleted:
        logger.info('Purged %d trashed task(s).', deleted)
