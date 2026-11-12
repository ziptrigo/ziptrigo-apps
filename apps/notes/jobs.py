"""The jobs the Notes app registers with `apps.core.scheduler` (wired up in `apps.py`).

Idempotent (the scheduler is at-least-once). A row that can't be processed is logged and skipped by
the service itself, so one bad note can't hold up the rest of the run.
"""

import logging

from .services import notes

logger = logging.getLogger(__name__)


def purge_trash() -> None:
    """Daily: delete the notes trashed longer than `NotesSettings.trash_days` ago."""
    deleted = notes.purge_trash()
    if deleted:
        logger.info('Purged %d trashed note(s).', deleted)
