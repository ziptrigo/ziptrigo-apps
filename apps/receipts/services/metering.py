"""Storage billing and account freezing/unfreezing.

**Daily metering** (`meter_account`, called per account by the `receipts.meter_storage` job): the
shared storage price (`BillingSettings`) times the account's `stored_bytes` -- originals,
previews and stripped emails, trashed ones included until purged -- is added to `accrued` once per
24 hours, and only the whole credits of it are spent
(`apps.billing.services.storage_charge`, floor; the remainder is carried), exactly like file
transfer's per-transfer metering but per account. `last_billed_at` advances by one period, not to
"now", so a tick landing a few seconds early never skips a day.

**Freezing.** If the owner can't cover the charge the account is *frozen* (`frozen_at`) and a
"storage paused" email is queued. Nothing is deleted. While frozen there are no previews, downloads
or processing (those services check `is_frozen`), and `last_billed_at` does not advance.

**Unfreezing** (`resume_account`, a task on the `receipts` queue, queued by the `credits_added`
receiver in `apps/receipts/apps.py` and also run by the daily job as a fallback): once the balance
covers at least one day of the account's storage (`daily_storage_cost`: the whole-credit cost of
its current `stored_bytes` at the shared price, at least 1) the account is unfrozen with
`last_billed_at = now` -- the frozen days are not billed afterwards -- and then the receipts
`waiting_for_credits` are processed, oldest first, stopping at the first one the balance can't
cover. A flat "1 credit" threshold would let an account whose daily charge is, say, 5 credits buy a
forgiven day with every 1-credit top-up; requiring a day's cost means the next daily run can pay.

The daily job (`jobs.meter_storage`) only offers the resume fallback to accounts that were
*already* frozen before its metering pass, so an account frozen in that very run isn't unfrozen
again minutes later with that day forgiven.
"""

import logging
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import F
from django.tasks import task
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
from apps.core.services.queue import enqueue_after_commit

from ..models import Receipt, ReceiptsAccount, ReceiptsSettings, ReceiptStatus
from .emails import send_storage_paused_email
from .processing import enqueue_processing

logger = logging.getLogger(__name__)

#: Minimum balance to unfreeze an account, whatever it stores.
MIN_BALANCE_TO_RESUME = 1

_BILLING_PERIOD = timedelta(hours=24)


def daily_storage_cost(stored_bytes: int, price_per_gb_per_day: Decimal | None = None) -> int:
    """The whole credits one day of storing `stored_bytes` costs, rounded up, at least
    `MIN_BALANCE_TO_RESUME`: the balance an account needs to be unfrozen."""
    if price_per_gb_per_day is None:
        price_per_gb_per_day = get_storage_price_per_gb_per_day()
    return max(storage_day_cost(stored_bytes, price_per_gb_per_day), MIN_BALANCE_TO_RESUME)


def meter_account(account_pk: object, price_per_gb_per_day: Decimal | None = None) -> None:
    """Charge one day of storage for the account if it is due. See the module docstring.

    Locks the account row and re-checks due-ness under the lock, so two runners of the
    at-least-once job can't both charge the same day.
    """
    if price_per_gb_per_day is None:
        price_per_gb_per_day = get_storage_price_per_gb_per_day()
    now = timezone.now()

    with transaction.atomic():
        account = (
            ReceiptsAccount.objects.select_for_update(of=('self',))
            .select_related('user')
            .get(pk=account_pk)
        )
        if account.frozen_at is not None:
            return
        if account.last_billed_at and now - account.last_billed_at < _BILLING_PERIOD:
            return

        next_billed_at = (account.last_billed_at or now) + _BILLING_PERIOD
        whole_credits, remainder = storage_charge(
            account.accrued, account.stored_bytes, price_per_gb_per_day
        )

        if whole_credits >= 1:
            try:
                spend_credits(
                    account.user,
                    whole_credits,
                    description=f'Receipts storage, day {account.billed_days + 1}',
                    source='receipts',
                )
            except InsufficientCreditsError:
                freeze_account(account)
                return

        ReceiptsAccount.objects.filter(pk=account.pk).update(
            accrued=remainder, last_billed_at=next_billed_at, billed_days=F('billed_days') + 1
        )


def freeze_account(account: ReceiptsAccount) -> None:
    """Freeze the account (storage unpaid) and tell its owner."""
    now = timezone.now()
    frozen = ReceiptsAccount.objects.filter(pk=account.pk, frozen_at__isnull=True).update(
        frozen_at=now
    )
    account.frozen_at = now
    if frozen:
        user_id = str(account.user_id)
        enqueue_after_commit(send_storage_paused_email, user_id)


def unfreeze_account(account: ReceiptsAccount) -> bool:
    """Unfreeze and restart the billing clock, so the frozen stretch is never billed. Returns
    whether this call did it."""
    now = timezone.now()
    won = ReceiptsAccount.objects.filter(pk=account.pk, frozen_at__isnull=False).update(
        frozen_at=None, last_billed_at=now
    )
    if won:
        account.frozen_at = None
        account.last_billed_at = now
    return bool(won)


@task(queue_name='receipts')
def resume_account(user_id: str) -> None:
    resume(user_id)


def resume(user_id: str) -> int:
    """Unfreeze the user's account if their balance covers a day of its storage, then queue their `waiting_for_credits`
    receipts oldest first, stopping at the first the balance can't cover. Returns how many were
    queued. Does nothing for a user without a Receipts account."""
    try:
        user = User.objects.get(pk=UUID(user_id))
    except User.DoesNotExist, ValueError:
        return 0
    account = ReceiptsAccount.objects.filter(user=user).first()
    if account is None:
        return 0

    balance = get_balance(user)
    if account.frozen_at is not None:
        if balance < daily_storage_cost(account.stored_bytes):
            return 0
        unfreeze_account(account)
        account.refresh_from_db()
        if account.frozen_at is not None:
            return 0

    price = ReceiptsSettings.load().price_per_file
    queued = 0
    waiting = Receipt.objects.filter(
        user=user,
        status=ReceiptStatus.WAITING_FOR_CREDITS,
        uploaded=True,
        deleted_at__isnull=True,
    ).order_by('created_at', 'id')
    for receipt in waiting:
        cost = 0 if receipt.charged_attempt_id == receipt.attempt_id else price
        if cost > balance:
            break
        balance -= cost
        enqueue_processing(receipt.pk)
        queued += 1
    return queued
