"""The four jobs `file_transfer` registers with `apps.core.scheduler` (spec section 10), wired up
from `apps/file_transfer/apps.py`. Every job is idempotent: the scheduler is at-least-once, so a
job can run twice for the same tick.
"""

from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import User

from .models import DownloadEvent, FileTransferSettings, Transfer, TransferStatus
from .services.downloads import downloads_remaining
from .services.emails import send_expires_soon_notification
from .services.lifecycle import end_transfer
from .services.metering import (
    delete_files_past_grace_period,
    meter_transfer,
    reenable_suspended_transfers_for_user,
)
from .services.uploads import abort_draft

#: Reminder window for "expires tomorrow" (spec section 8): sent once, one day before a
#: time-based expiry.
_EXPIRY_REMINDER_WINDOW = timedelta(hours=24)

#: How old a draft (or, in phase 2, unconfirmed) transfer needs to be before cleanup deletes it
#: (spec section 2 defaults).
_DRAFT_MAX_AGE = timedelta(hours=24)

#: How long a download event keeps its IP before it's purged (spec section 12).
_DOWNLOAD_IP_MAX_AGE = timedelta(days=90)


def meter_transfers() -> None:
    """Daily: charge one day per active transfer, suspend on insufficient credits, re-enable
    transfers whose owner has topped up (fallback -- the `credits_added` signal is the fast
    path), and delete files for transfers past their suspension grace period."""
    settings_row = FileTransferSettings.load()

    for transfer in Transfer.objects.filter(status=TransferStatus.ACTIVE, owner__isnull=False):
        meter_transfer(transfer, settings_row)

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
        reenable_suspended_transfers_for_user(owner)

    delete_files_past_grace_period(settings_row)


def expire_transfers() -> None:
    """Every 5 minutes: end transfers past their expiry or (as a fallback -- the download view
    already handles this inline) past their max-downloads limit, deleting their objects
    immediately; send "expires tomorrow" reminders once."""
    now = timezone.now()

    expired = Transfer.objects.filter(status=TransferStatus.ACTIVE, expires_at__lte=now)
    for transfer in expired:
        end_transfer(transfer, TransferStatus.EXPIRED, delete_files=True)

    for transfer in Transfer.objects.filter(
        status=TransferStatus.ACTIVE, max_downloads__isnull=False
    ):
        remaining = downloads_remaining(transfer)
        if remaining is not None and remaining <= 0:
            end_transfer(transfer, TransferStatus.EXPIRED, delete_files=True)

    reminder_cutoff = now + _EXPIRY_REMINDER_WINDOW
    due_reminders = Transfer.objects.filter(
        status=TransferStatus.ACTIVE,
        expires_at__isnull=False,
        expires_at__gt=now,
        expires_at__lte=reminder_cutoff,
        expiry_notified_at__isnull=True,
    )
    for transfer in due_reminders:
        send_expires_soon_notification.enqueue(str(transfer.id))
        Transfer.objects.filter(pk=transfer.pk).update(expiry_notified_at=now)


def cleanup_drafts() -> None:
    """Hourly: delete drafts (and, in phase 2, unconfirmed transfers) older than 24 hours,
    aborting their multipart uploads."""
    cutoff = timezone.now() - _DRAFT_MAX_AGE
    stale = Transfer.objects.filter(
        status__in=[TransferStatus.DRAFT, TransferStatus.PENDING_CONFIRMATION],
        created_at__lte=cutoff,
    )
    for transfer in stale:
        abort_draft(transfer)


def purge_download_ips() -> None:
    """Daily: null out the IP on download events older than 90 days (spec section 12)."""
    cutoff = timezone.now() - _DOWNLOAD_IP_MAX_AGE
    DownloadEvent.objects.filter(created_at__lte=cutoff, ip__isnull=False).update(ip=None)
