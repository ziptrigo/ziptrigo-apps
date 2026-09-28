"""The four jobs `file_transfer` registers with `apps.core.scheduler` (spec section 10), wired up
from `apps/file_transfer/apps.py`. Every job is idempotent: the scheduler is at-least-once, so a
job can run twice for the same tick.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User

from .models import ENDED_STATUSES, DownloadEvent, FileTransferSettings, Transfer, TransferStatus
from .services.downloads import downloads_remaining
from .services.emails import send_expires_soon_notification
from .services.lifecycle import end_transfer, finish_deferred_deletion
from .services.metering import (
    delete_files_past_grace_period,
    meter_transfer,
    reenable_suspended_transfers_for_user,
)
from .services.storage import GET_URL_EXPIRES_SECONDS
from .services.uploads import abort_draft

logger = logging.getLogger(__name__)

#: Reminder window for "expires tomorrow" (spec section 8): sent once, one day before a
#: time-based expiry.
_EXPIRY_REMINDER_WINDOW = timedelta(hours=24)

#: Never remind about an expiry that was always going to land inside this window (a 1-day
#: transfer, say) -- there'd be nothing "tomorrow" about it and it would fire on the very next
#: tick after sending.
_MIN_LIFETIME_FOR_REMINDER = timedelta(hours=24)

#: How old a draft (or, in phase 2, unconfirmed) transfer needs to be before cleanup deletes it
#: (spec section 2 defaults).
_DRAFT_MAX_AGE = timedelta(hours=24)

#: How long a download event keeps its IP before it's purged (spec section 12).
_DOWNLOAD_IP_MAX_AGE = timedelta(days=90)

#: Statuses `expire_transfers` also ends on time-based expiry, alongside `ACTIVE` -- a `DISABLED`
#: transfer past its own `expires_at` should still expire (and get its files cleaned up) rather
#: than sit forever; a `SUSPENDED` one is left alone here since the grace-period path
#: (`metering.delete_files_past_grace_period`) already ends it eventually.
_EXPIRABLE_STATUSES = (TransferStatus.ACTIVE, TransferStatus.DISABLED)


def meter_transfers() -> None:
    """Daily: charge one day per active transfer, suspend on insufficient credits, re-enable
    transfers whose owner has topped up (fallback -- the `credits_added` signal is the fast
    path), and delete files for transfers past their suspension grace period."""
    settings_row = FileTransferSettings.load()

    for transfer in Transfer.objects.filter(status=TransferStatus.ACTIVE, owner__isnull=False):
        try:
            meter_transfer(transfer, settings_row)
        except Exception:
            logger.exception('meter_transfer failed for transfer %s', transfer.pk)

    owner_ids = (
        Transfer.objects.filter(status=TransferStatus.SUSPENDED, owner__isnull=False)
        .values_list('owner_id', flat=True)
        .distinct()
    )
    for owner_id in owner_ids:
        try:
            owner = User.objects.get(pk=owner_id)
        except User.DoesNotExist:
            continue
        try:
            reenable_suspended_transfers_for_user(owner)
        except Exception:
            logger.exception('reenable_suspended_transfers_for_user failed for user %s', owner_id)

    try:
        delete_files_past_grace_period(settings_row)
    except Exception:
        logger.exception('delete_files_past_grace_period failed')


def expire_transfers() -> None:
    """Every 5 minutes: end transfers past their expiry or (as a fallback -- the download view
    already handles this inline) past their max-downloads limit, deleting their objects
    immediately; send "expires tomorrow" reminders once; and finish deleting the files of any
    transfer whose deletion was deferred (the last download reaching `max_downloads`) once its
    presigned URL has safely expired.
    """
    now = timezone.now()

    expired = Transfer.objects.filter(status__in=_EXPIRABLE_STATUSES, expires_at__lte=now)
    for transfer in expired:
        try:
            end_transfer(transfer, TransferStatus.EXPIRED, delete_files=True, notify=True)
        except Exception:
            logger.exception('Failed to expire transfer %s', transfer.pk)

    for transfer in Transfer.objects.filter(
        status=TransferStatus.ACTIVE, max_downloads__isnull=False
    ):
        try:
            remaining = downloads_remaining(transfer)
            if remaining is not None and remaining <= 0:
                end_transfer(transfer, TransferStatus.EXPIRED, delete_files=False)
        except Exception:
            logger.exception('Failed to end transfer %s past its download limit', transfer.pk)

    reminder_cutoff = now + _EXPIRY_REMINDER_WINDOW
    due_reminders = Transfer.objects.filter(
        status=TransferStatus.ACTIVE,
        expires_at__isnull=False,
        expires_at__gt=now,
        expires_at__lte=reminder_cutoff,
        expiry_notified_at__isnull=True,
    )
    for transfer in due_reminders:
        if (
            transfer.completed_at
            and transfer.expires_at
            and transfer.expires_at - transfer.completed_at <= _MIN_LIFETIME_FOR_REMINDER
        ):
            # A transfer that was only ever going to live a day (or less) doesn't need an
            # "expires tomorrow" reminder -- it would fire on the very next tick after sending.
            continue
        try:
            send_expires_soon_notification.enqueue(str(transfer.id))
            Transfer.objects.filter(pk=transfer.pk).update(expiry_notified_at=now)
        except Exception:
            logger.exception('Failed to send expiry reminder for transfer %s', transfer.pk)

    deletion_cutoff = now - timedelta(seconds=GET_URL_EXPIRES_SECONDS)
    pending_deletion = Transfer.objects.filter(
        status__in=ENDED_STATUSES, files_deleted_at__isnull=True, ended_at__lte=deletion_cutoff
    )
    for transfer in pending_deletion:
        try:
            finish_deferred_deletion(transfer, notify=True)
        except Exception:
            logger.exception('Failed to finish deferred deletion for transfer %s', transfer.pk)


def cleanup_drafts() -> None:
    """Hourly: delete drafts (and, in phase 2, unconfirmed transfers) older than 24 hours,
    aborting their multipart uploads."""
    cutoff = timezone.now() - _DRAFT_MAX_AGE
    stale = Transfer.objects.filter(
        status__in=[TransferStatus.DRAFT, TransferStatus.PENDING_CONFIRMATION],
        created_at__lte=cutoff,
    )
    for transfer in stale:
        try:
            abort_draft(transfer)
        except Exception:
            logger.exception('Failed to abort draft transfer %s', transfer.pk)


def purge_download_ips() -> None:
    """Daily: null out the IP on download events older than 90 days (spec section 12)."""
    cutoff = timezone.now() - _DOWNLOAD_IP_MAX_AGE
    DownloadEvent.objects.filter(created_at__lte=cutoff, ip__isnull=False).update(ip=None)
