"""The jobs the Notes app registers with `apps.core.scheduler` (wired up in `apps.py`).

Idempotent (the scheduler is at-least-once). A row that can't be processed is logged and skipped by
the service itself, so one bad note can't hold up the rest of the run.
"""

import logging

from django.utils import timezone

from .models import Note
from .services import billing, notes

logger = logging.getLogger(__name__)


def purge_trash() -> None:
    """Daily: delete the notes trashed longer than `NotesSettings.trash_days` ago."""
    deleted = notes.purge_trash()
    if deleted:
        logger.info('Purged %d trashed note(s).', deleted)


def renew() -> None:
    """Daily: renew the paid notes that are due (locking the ones the balance can't cover), unlock
    the notes that were locked before this run if their owner can pay now, and warn the users whose
    balance won't cover the renewals coming up.

    A note this very run locks has `locked_at == run_start`, so `resume(locked_before=run_start)`
    leaves it alone (offering it a resume at once would forgive its day).
    """
    run_start = timezone.now()
    result = billing.renew_due(run_start)
    if result.renewed or result.locked_count:
        logger.info('Renewed %d note(s), locked %d.', result.renewed, result.locked_count)

    user_ids = list(
        Note.objects.filter(
            locked_at__isnull=False, locked_at__lt=run_start, deleted_at__isnull=True
        )
        .order_by('user_id')
        .values_list('user_id', flat=True)
        .distinct()
    )
    for user_id in user_ids:
        try:
            billing.resume(str(user_id), locked_before=run_start)
        except Exception:
            logger.exception('Could not resume the notes of user %s', user_id)

    warned = billing.warn_upcoming(run_start)
    if warned:
        logger.info('Warned %d user(s) about upcoming note renewals.', warned)
