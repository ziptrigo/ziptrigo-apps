"""The jobs the To-do app registers with `apps.core.scheduler` (wired up in `apps.py`).

Idempotent (the scheduler is at-least-once). A row that can't be processed is logged and skipped by
the service itself, so one bad task can't hold up the rest of the run.
"""

import logging

from django.utils import timezone

from apps.billing.services import get_storage_price_per_gb_per_day

from .models import TodoAccount
from .services import attachments, inbound, reminders, tasks
from .services.metering import meter_account, resume

logger = logging.getLogger(__name__)


def purge_trash() -> None:
    """Daily: delete the tasks trashed longer than `TodoSettings.trash_days` ago."""
    deleted = tasks.purge_trash()
    if deleted:
        logger.info('Purged %d trashed task(s).', deleted)


def meter_storage() -> None:
    """Daily: charge each account with stored bytes one day of attachment storage, and give
    frozen accounts a chance to resume."""
    price = get_storage_price_per_gb_per_day()
    # Snapshot who is frozen *before* metering: an account this run freezes must not be offered
    # a resume in the same run (it would be unfrozen at once, its day forgiven).
    already_frozen = list(
        TodoAccount.objects.filter(frozen_at__isnull=False).values_list('pk', flat=True)
    )
    for account_pk in list(
        TodoAccount.objects.filter(stored_bytes__gt=0, frozen_at__isnull=True).values_list(
            'pk', flat=True
        )
    ):
        try:
            meter_account(account_pk, price)
        except Exception:
            logger.exception('meter_account failed for account %s', account_pk)

    for account_pk in already_frozen:
        try:
            resume(str(account_pk))
        except Exception:
            logger.exception('resume failed for account %s', account_pk)


def cleanup_uploads() -> None:
    """Hourly: remove attachment uploads that were started but never completed within 24 hours."""
    removed = attachments.abandon_uploads()
    if removed:
        logger.info('Removed %d abandoned attachment upload(s).', removed)


def send_reminders() -> None:
    """Every minute: deliver the reminders that are due (in-app, by email or both) and mark the
    ones too old to deliver as sent (`services.reminders.deliver_due`)."""
    delivered = reminders.deliver_due(timezone.now())
    if delivered:
        logger.info('Delivered %d reminder(s).', delivered)


def poll_inbound() -> None:
    """Every minute: register the messages SES dropped under `todo/inbound/` and queue their
    ingest (`services.inbound.poll`). Errors propagate: the scheduler records them as the job's
    `last_error`."""
    inbound.poll()


def purge_inbound_messages() -> None:
    """Daily: delete the bookkeeping rows (and objects) of old `done` messages that created no
    email row (`services.inbound.purge_done_messages`). Errors propagate."""
    purged = inbound.purge_done_messages()
    if purged:
        logger.info('Purged %d old inbound message(s).', purged)
