"""Daily metering (spec section 7): charge every active, owned transfer a fraction of a credit
per day, spend whole credits once `accrued` reaches 1, suspend a transfer that runs out of
credits, and re-enable one that's topped back up.

Re-enabling (the daily fallback, the `credits_added` receiver and the dashboard/API action) needs
the balance to cover that transfer's own next day (`daily_cost`), not a flat 1 credit: otherwise a
transfer costing 3 a day for an owner holding 2 would be suspended and re-enabled with its day
forgiven on every run, i.e. stored for free. `reenable_suspended_transfers_for_user` applies that
rule to a user's suspended transfers; `actions.reenable_transfer_action` uses `daily_cost` for one.

`meter_transfer` is called once per transfer by the `meter_transfers` job (`apps/file_transfer/jobs.py`)
and does the actual charging; `reenable_suspended_transfers_for_user` is called both by that job
(as a fallback) and by the `credits_added` signal receiver in `apps/file_transfer/apps.py` (the
fast path).
"""

import logging
from collections.abc import Collection
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.services import (
    InsufficientCreditsError,
    get_balance,
    get_storage_price_per_gb_per_day,
    spend_credits,
    storage_charge,
    storage_day_cost,
)
from apps.core.services import notifications

from ..models import FileTransferSettings, Transfer, TransferStatus
from .emails import send_suspended_notification
from .lifecycle import end_transfer
from .naming import transfer_display_name

logger = logging.getLogger(__name__)

#: Minimum balance to re-enable a suspended transfer (spec section 7); `daily_cost` is never less.
MIN_BALANCE_TO_REENABLE = 1

_BILLING_PERIOD = timedelta(hours=24)


def daily_cost(transfer: Transfer, price_per_gb_per_day: Decimal | None = None) -> int:
    """The balance an owner needs to re-enable `transfer`: one day of its storage in whole credits,
    rounded up, at least `MIN_BALANCE_TO_REENABLE`."""
    if price_per_gb_per_day is None:
        price_per_gb_per_day = get_storage_price_per_gb_per_day()
    # `storage_day_cost` already returns at least 1, so the `max` is redundant today. It stays on
    # purpose: `MIN_BALANCE_TO_REENABLE` is spec section 7's own floor, and keeping it here means
    # this function can't drop below it if the shared formula's minimum ever changes.
    return max(storage_day_cost(transfer.size_bytes, price_per_gb_per_day), MIN_BALANCE_TO_REENABLE)


def meter_transfer(transfer: Transfer, price_per_gb_per_day: Decimal | None = None) -> None:
    """Charge one day's storage for `transfer`, if it's due (`last_billed_at` at least 24h old).

    The price is the site-wide storage price (`BillingSettings`, shared with every other product
    that stores files); a caller metering many transfers in a row loads it once and passes it as
    `price_per_gb_per_day`, otherwise it's read here.

    Rounds down (`apps.billing.services.storage_charge`): `accrued` (a Decimal) collects
    `price x size_in_gb` every day, and only the floor of that -- whenever it's at least 1 -- is
    actually spent, keeping the remainder. Suspends the transfer (and emails the owner) if the
    owner can't cover a whole credit. Never charges a suspended, disabled, expired or deleted
    transfer.

    Locks the transfer row for the duration of the charge, re-checking due-ness once the lock is
    held (a concurrent run of the same at-least-once job must not double-charge the same day), and
    advances `last_billed_at` by exactly one billing period rather than to "now": jumping to "now"
    would let a tick that lands a few seconds early each day never quite reach the 24h mark,
    silently skipping a day's charge (and, for a transfer that's several days overdue -- the
    process was down, say -- charging one day per run still lets it catch up one period at a time
    without ever billing for days it shouldn't). A transfer that stops being metered (disabled,
    suspended) simply stops advancing `last_billed_at` until `reenable_transfer` resets it to
    "now", so no backlog ever accumulates across a disabled or suspended stretch.
    """
    if price_per_gb_per_day is None:
        price_per_gb_per_day = get_storage_price_per_gb_per_day()
    now = timezone.now()

    with transaction.atomic():
        locked = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if locked.status != TransferStatus.ACTIVE or locked.owner is None:
            return
        if locked.held_for_review_at:
            # On hold pending abuse review (issue #59): unavailable, and not billed for the days
            # it spends held -- `last_billed_at` simply doesn't advance while this is set, the
            # same way it doesn't advance while disabled/suspended, so no backlog accumulates
            # across a held stretch either (see this function's own docstring).
            return
        if locked.last_billed_at and now - locked.last_billed_at < _BILLING_PERIOD:
            return

        next_billed_at = (locked.last_billed_at or now) + _BILLING_PERIOD
        whole_credits, remainder = storage_charge(
            locked.accrued, locked.size_bytes, price_per_gb_per_day
        )
        billed_days = locked.billed_days + 1

        if whole_credits < 1:
            Transfer.objects.filter(pk=locked.pk).update(
                accrued=remainder, last_billed_at=next_billed_at, billed_days=F('billed_days') + 1
            )
            transfer.accrued = remainder
            transfer.last_billed_at = next_billed_at
            transfer.billed_days = billed_days
            return

        description = f'Transfer "{transfer_display_name(locked)}", day {billed_days}'
        try:
            spend_credits(
                locked.owner, whole_credits, description=description, source='file_transfer'
            )
        except InsufficientCreditsError:
            suspend_transfer(locked)
            transfer.status = locked.status
            transfer.suspended_at = locked.suspended_at
            return

        Transfer.objects.filter(pk=locked.pk).update(
            accrued=remainder,
            last_billed_at=next_billed_at,
            billed_days=F('billed_days') + 1,
            credits_charged=F('credits_charged') + whole_credits,
        )
        transfer.accrued = remainder
        transfer.last_billed_at = next_billed_at
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
    _notify_suspended(transfer)


def _notify_suspended(transfer: Transfer) -> None:
    """The in-app twin of the suspension email (issue #169). Written in the caller's transaction
    (`meter_transfer` suspends inside one), so it exists exactly when the suspension does; a
    failure to write it is logged and must not undo the suspension or abort the metering run."""
    if transfer.owner_id is None:
        return
    try:
        grace_days = FileTransferSettings.load().suspension_grace_days
        notifications.notify(
            transfer.owner_id,
            source='file_transfer',
            title=f'Transfer suspended: {transfer_display_name(transfer)}',
            body=(
                'It ran out of credits and its download link no longer works. Add credits within '
                f'{grace_days} day(s) to resume it, or its files will be deleted.'
            ),
            url=reverse('file_transfer:dashboard'),
        )
    except Exception:
        logger.exception('Could not notify the owner that transfer %s was suspended', transfer.pk)


def reenable_transfer(transfer: Transfer) -> bool:
    """Resume a suspended or disabled transfer; returns whether this call re-enabled it.

    The write is conditional on the status the caller read (`transfer.status`), so racing the
    grace-period delete, a dashboard delete-now or another re-enable can't resurrect a `DELETED`
    transfer whose files are gone (and then meter it): when no row matched, nothing changed and
    this returns `False`.

    The billing clock: a *suspended* transfer's was stopped at its last paid day and restarts at
    now (spec section 7). A *disabled* one keeps its `last_billed_at`, but no earlier than one
    period ago: resetting it to now would let an owner disable and re-enable a transfer before its
    day is due, over and over, and never be charged; keeping it as it was means the day in
    progress still falls due on schedule, while the clamp keeps a long disabled stretch from
    turning into a backlog of days charged one per run (`meter_transfer`'s "no backlog across a
    disabled stretch").
    """
    expected = transfer.status
    if expected not in (TransferStatus.SUSPENDED, TransferStatus.DISABLED):
        return False
    now = timezone.now()
    last_billed_at = transfer.last_billed_at
    if expected == TransferStatus.SUSPENDED:
        last_billed_at = now
    elif last_billed_at is not None:
        last_billed_at = max(last_billed_at, now - _BILLING_PERIOD)
    updated = Transfer.objects.filter(pk=transfer.pk, status=expected).update(
        status=TransferStatus.ACTIVE, suspended_at=None, last_billed_at=last_billed_at
    )
    if not updated:
        return False
    transfer.status = TransferStatus.ACTIVE
    transfer.suspended_at = None
    transfer.last_billed_at = last_billed_at
    return True


def reenable_suspended_transfers_for_user(
    user: User, transfer_ids: Collection[object] | None = None
) -> int:
    """Re-enable `user`'s suspended transfers that the balance can pay a day for (called from the
    `credits_added` signal receiver, and as the daily job's fallback); returns the number re-enabled.

    Oldest suspension first, each needing its own `daily_cost` on top of those already re-enabled
    in this call, stopping at the first the balance can't cover. `transfer_ids` limits it to those
    transfers: the daily job passes the ones suspended *before* its metering pass, so a transfer
    suspended in that very run isn't re-enabled at once with the day it couldn't pay forgiven.

    Limits, on purpose: the running sum reserves nothing for the owner's *active* transfers' next
    charges, so a balance can re-enable a transfer and then fall short for the others on the next
    run -- bounded, not a loop, since a transfer suspended by a run is not re-enabled by that run.
    Nothing is spent here, so two concurrent calls (the receiver racing the daily job) can each
    count the same balance and together re-enable more than it covers; each re-enabled transfer
    then pays or is suspended again at its own next due time, with the same bound. A transfer on
    hold for abuse review (issue #59) is unavailable and unbilled, so it is re-enabled without
    counting toward the sum and doesn't use up the balance other transfers need.
    """
    balance = get_balance(user)
    if balance < MIN_BALANCE_TO_REENABLE:
        return 0

    suspended = Transfer.objects.filter(owner=user, status=TransferStatus.SUSPENDED)
    if transfer_ids is not None:
        suspended = suspended.filter(pk__in=transfer_ids)
    price = get_storage_price_per_gb_per_day()
    count = 0
    for transfer in suspended.order_by('suspended_at', 'created_at'):
        cost = 0 if transfer.held_for_review_at else daily_cost(transfer, price)
        if balance - cost < 0:
            break
        if reenable_transfer(transfer):
            balance -= cost
            count += 1
    return count


def delete_files_past_grace_period(settings_row: FileTransferSettings | None = None) -> int:
    """End (and delete the files of) every transfer suspended for longer than the grace period
    without being topped up. Returns the number ended.

    Ending is a claim on the `SUSPENDED` status, so a transfer a top-up re-enabled (or the owner
    deleted) after the query above is skipped: no files deleted, no email, not counted."""
    settings_row = settings_row or FileTransferSettings.load()
    cutoff = timezone.now() - timedelta(days=settings_row.suspension_grace_days)
    transfers = Transfer.objects.filter(status=TransferStatus.SUSPENDED, suspended_at__lte=cutoff)

    count = 0
    for transfer in transfers:
        if end_transfer(
            transfer,
            TransferStatus.DELETED,
            delete_files=True,
            notify=True,
            from_statuses=(TransferStatus.SUSPENDED,),
            # Not re-suspended since the query: a fresh suspension starts a fresh grace period.
            only_if=Q(suspended_at__lte=cutoff),
        ):
            count += 1
    return count
