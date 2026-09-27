"""Daily metering (spec section 7): charge every active, owned transfer a fraction of a credit
per day, spend whole credits once `accrued` reaches 1, suspend a transfer that runs out of
credits, and re-enable one that's topped back up.

`meter_transfer` is called once per transfer by the `meter_transfers` job (`apps/file_transfer/jobs.py`)
and does the actual charging; `reenable_if_topped_up` is called both by that job (as a fallback)
and by the `credits_added` signal receiver in `apps/file_transfer/apps.py` (the fast path).
"""

from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from django.utils import timezone

from apps.accounts.models import User
from apps.billing.services import InsufficientCreditsError, get_balance, spend_credits

from ..models import FileTransferSettings, Transfer, TransferStatus
from .emails import send_suspended_notification
from .lifecycle import end_transfer
from .naming import transfer_display_name

#: Minimum balance to re-enable a suspended transfer (spec section 7).
MIN_BALANCE_TO_REENABLE = 1

_BYTES_PER_GB = Decimal(1024**3)
_BILLING_PERIOD = timedelta(hours=24)


def _size_in_gb(size_bytes: int) -> Decimal:
    return Decimal(size_bytes) / _BYTES_PER_GB


def meter_transfer(transfer: Transfer, settings_row: FileTransferSettings | None = None) -> None:
    """Charge one day's storage for `transfer`, if it's due (`last_billed_at` at least 24h old).

    Rounds down: `accrued` (a Decimal) collects `price_per_gb_per_day x size_in_gb` every day, and
    only the floor of that -- whenever it's at least 1 -- is actually spent, keeping the
    remainder. Suspends the transfer (and emails the owner) if the owner can't cover a whole
    credit. Never charges a suspended, disabled, expired or deleted transfer.
    """
    if transfer.status != TransferStatus.ACTIVE or transfer.owner is None:
        return

    now = timezone.now()
    if transfer.last_billed_at and now - transfer.last_billed_at < _BILLING_PERIOD:
        return

    settings_row = settings_row or FileTransferSettings.load()
    accrued = transfer.accrued + settings_row.price_per_gb_per_day * _size_in_gb(
        transfer.size_bytes
    )
    whole_credits = int(accrued.to_integral_value(rounding=ROUND_FLOOR))
    billed_days = transfer.billed_days + 1

    if whole_credits < 1:
        Transfer.objects.filter(pk=transfer.pk).update(
            accrued=accrued, last_billed_at=now, billed_days=billed_days
        )
        transfer.accrued = accrued
        transfer.last_billed_at = now
        transfer.billed_days = billed_days
        return

    description = f'Transfer "{transfer_display_name(transfer)}", day {billed_days}'
    try:
        spend_credits(
            transfer.owner, whole_credits, description=description, source='file_transfer'
        )
    except InsufficientCreditsError:
        suspend_transfer(transfer)
        return

    remainder = accrued - whole_credits
    Transfer.objects.filter(pk=transfer.pk).update(
        accrued=remainder,
        last_billed_at=now,
        billed_days=billed_days,
        credits_charged=transfer.credits_charged + whole_credits,
    )
    transfer.accrued = remainder
    transfer.last_billed_at = now
    transfer.billed_days = billed_days
    transfer.credits_charged += whole_credits


def suspend_transfer(transfer: Transfer) -> None:
    """Suspend `transfer` for lack of credits: the link stops working, no more days are charged,
    and the owner is emailed with the grace-period deadline."""
    now = timezone.now()
    Transfer.objects.filter(pk=transfer.pk).update(
        status=TransferStatus.SUSPENDED, suspended_at=now
    )
    transfer.status = TransferStatus.SUSPENDED
    transfer.suspended_at = now
    send_suspended_notification.enqueue(str(transfer.id))


def reenable_transfer(transfer: Transfer) -> None:
    """Resume a suspended transfer: resets the billing clock to now (spec section 7)."""
    now = timezone.now()
    Transfer.objects.filter(pk=transfer.pk).update(
        status=TransferStatus.ACTIVE, suspended_at=None, last_billed_at=now
    )
    transfer.status = TransferStatus.ACTIVE
    transfer.suspended_at = None
    transfer.last_billed_at = now


def reenable_suspended_transfers_for_user(user: User) -> int:
    """Re-enable every one of `user`'s suspended transfers, now that they've topped up (called
    from the `credits_added` signal receiver, and as the daily job's fallback). Only re-enables
    when the balance covers the 1-credit minimum; returns the number re-enabled."""
    if get_balance(user) < MIN_BALANCE_TO_REENABLE:
        return 0

    suspended = list(Transfer.objects.filter(owner=user, status=TransferStatus.SUSPENDED))
    for transfer in suspended:
        reenable_transfer(transfer)
    return len(suspended)


def delete_files_past_grace_period(settings_row: FileTransferSettings | None = None) -> int:
    """End (and delete the files of) every transfer suspended for longer than the grace period
    without being topped up. Returns the number ended."""
    settings_row = settings_row or FileTransferSettings.load()
    cutoff = timezone.now() - timedelta(days=settings_row.suspension_grace_days)
    transfers = Transfer.objects.filter(status=TransferStatus.SUSPENDED, suspended_at__lte=cutoff)

    count = 0
    for transfer in transfers:
        end_transfer(transfer, TransferStatus.DELETED, delete_files=True, notify=True)
        count += 1
    return count
