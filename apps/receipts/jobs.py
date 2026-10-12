"""The four jobs `receipts` registers with `apps.core.scheduler`, wired up from
`apps/receipts/apps.py`. The scheduler is at-least-once, so every job is idempotent, and each does
its per-row work in a `try`/`except` that logs and carries on, so one bad row can't abort the rest
of a run.

- `meter_storage` (daily): bill each account's storage; unfreeze frozen accounts that have topped up
  (a fallback -- the `credits_added` receiver is the fast path).
- `purge_trash` (daily): delete receipts that have been in the Trash for `trash_days`.
- `retry_due` (every 5 minutes): re-queue receipts whose retry time has come, ones stuck
  `processing`, and ones that never got queued.
- `cleanup_uploads` (hourly): remove uploads never completed within 24 hours.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from apps.billing.services import get_storage_price_per_gb_per_day

from .models import Receipt, ReceiptsAccount, ReceiptsSettings, ReceiptStatus
from .services.lifecycle import purge_receipt
from .services.metering import meter_account, resume
from .services.processing import enqueue_processing, requeue_stuck
from .services.uploads import ABANDONED_AFTER, abandon_upload

logger = logging.getLogger(__name__)

#: A receipt `processing` for longer than this lost its worker (an AI call is bounded by
#: `RECEIPTS_AI_TIMEOUT_SECONDS`, minutes at the very most).
STUCK_AFTER = timedelta(minutes=30)

#: A `received` receipt nobody has claimed for this long probably never got queued (a crash between
#: committing it and enqueueing the task); queue it again. Claiming is idempotent, so a duplicate
#: task is harmless.
UNQUEUED_AFTER = timedelta(minutes=15)


def meter_storage() -> None:
    """Daily: charge each account with stored bytes one day of storage, and give frozen accounts
    a chance to resume."""
    price = get_storage_price_per_gb_per_day()
    for account_pk in list(
        ReceiptsAccount.objects.filter(stored_bytes__gt=0, frozen_at__isnull=True).values_list(
            'pk', flat=True
        )
    ):
        try:
            meter_account(account_pk, price)
        except Exception:
            logger.exception('meter_account failed for account %s', account_pk)

    for account_pk in list(
        ReceiptsAccount.objects.filter(frozen_at__isnull=False).values_list('pk', flat=True)
    ):
        try:
            resume(str(account_pk))
        except Exception:
            logger.exception('resume failed for account %s', account_pk)


def purge_trash() -> None:
    """Daily: delete the S3 objects and rows of receipts trashed more than `trash_days` ago (and
    the stripped `.eml` along with an email's last receipt)."""
    cutoff = timezone.now() - timedelta(days=ReceiptsSettings.load().trash_days)
    for receipt in Receipt.objects.filter(deleted_at__lte=cutoff).select_related('inbound_email'):
        try:
            purge_receipt(receipt)
        except Exception:
            logger.exception('Failed to purge receipt %s', receipt.pk)


def retry_due() -> None:
    """Every 5 minutes: queue receipts waiting for a retry, recover receipts stuck in
    `processing`, and queue ones that were never picked up."""
    now = timezone.now()
    base = Receipt.objects.filter(uploaded=True, deleted_at__isnull=True)

    due = base.filter(status=ReceiptStatus.RECEIVED, next_attempt_at__lte=now)
    unqueued = base.filter(
        status=ReceiptStatus.RECEIVED,
        next_attempt_at__isnull=True,
        updated_at__lte=now - UNQUEUED_AFTER,
    )
    for receipt_id in [*due.values_list('pk', flat=True), *unqueued.values_list('pk', flat=True)]:
        try:
            enqueue_processing(receipt_id)
        except Exception:
            logger.exception('Failed to queue receipt %s', receipt_id)

    stuck = base.filter(
        status=ReceiptStatus.PROCESSING, processing_started_at__lt=now - STUCK_AFTER
    ).values_list('pk', flat=True)
    for receipt_id in list(stuck):
        try:
            if requeue_stuck(receipt_id, STUCK_AFTER):
                logger.warning('Receipt %s was stuck processing; requeued', receipt_id)
        except Exception:
            logger.exception('Failed to recover stuck receipt %s', receipt_id)


def cleanup_uploads() -> None:
    """Hourly: remove uploads that were started but never completed within 24 hours."""
    cutoff = timezone.now() - ABANDONED_AFTER
    for receipt in Receipt.objects.filter(uploaded=False, created_at__lte=cutoff):
        try:
            abandon_upload(receipt)
        except Exception:
            logger.exception('Failed to clean up abandoned upload %s', receipt.pk)
