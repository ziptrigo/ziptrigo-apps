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
    """Daily: renew the paid notes that are due (locking the ones the balance can't cover),
    unlock the notes that were already locked before this run if their owner can pay now, and warn
    the users whose balance won't cover the renewals coming up.

    The notes locked before the run are snapshotted first: one this very run locks must not be
    offered a resume at once (the day would be forgiven).
    """
    now = timezone.now()
    already_locked: dict = {}
    for note_id, user_id in Note.objects.filter(
        locked_at__isnull=False, deleted_at__isnull=True
    ).values_list('pk', 'user_id'):
        already_locked.setdefault(user_id, set()).add(note_id)

    result = billing.renew_due(now)
    if result.renewed or result.locked_count:
        logger.info('Renewed %d note(s), locked %d.', result.renewed, result.locked_count)

    for user_id, note_ids in already_locked.items():
        try:
            billing.resume(str(user_id), only=note_ids)
        except Exception:
            logger.exception('Could not resume the notes of user %s', user_id)

    warned = billing.warn_upcoming(now)
    if warned:
        logger.info('Warned %d user(s) about upcoming note renewals.', warned)
