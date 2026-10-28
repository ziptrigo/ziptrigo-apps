"""Attachment storage billing and account freezing/unfreezing (issue #184), the twin of
`apps.receipts.services.metering` without the receipts to resume.

**Daily metering** (`meter_account`, called per account by the `todo.meter_storage` job): the shared
storage price (`BillingSettings`) times the account's `stored_bytes` -- every uploaded attachment,
those of trashed tasks included until they are purged -- is added to `accrued` once per 24 hours,
and only the whole credits of it are spent (`apps.billing.services.storage_charge`, floor; the
remainder is carried). `last_billed_at` advances by one period, not to "now", so a tick landing a
few seconds early never skips a day.

**Freezing.** If the owner can't cover the charge the account is *frozen* (`frozen_at`), a "storage
paused" email is queued and an in-app notification is written. Nothing is deleted. While frozen,
uploads and downloads are refused (`AccountFrozenError`) and `last_billed_at` does not advance.

**Unfreezing** (`resume`, the `resume_account` task queued by `billing.on_credits_added` when
credits arrive, and also run by the daily job as a fallback): once the balance covers at least one
day of the account's storage (`daily_storage_cost`: the whole-credit cost of its current
`stored_bytes` at the shared price, at least 1) the account is unfrozen with `last_billed_at = now`
-- the frozen days are not billed afterwards. A flat "1 credit" threshold would let an account
whose daily charge is, say, 5 credits buy a forgiven day with every 1-credit top-up.

The daily job only offers the resume fallback to accounts that were *already* frozen before its
metering pass, so an account frozen in that very run isn't unfrozen again minutes later with that
day forgiven.
"""

import logging
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import F
from django.tasks import task
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
from apps.core.services.queue import enqueue_after_commit

from ..models import TodoAccount
from .emails import send_storage_paused_email

logger = logging.getLogger(__name__)

SOURCE = 'todo'

#: Minimum balance to unfreeze an account, whatever it stores.
MIN_BALANCE_TO_RESUME = 1

_BILLING_PERIOD = timedelta(hours=24)

#: `Notification.key` of the in-app "storage paused" notice: one per user is enough, since an
#: account is frozen at most once at a time, and unfreezing deletes it by this key.
FROZEN_NOTICE_KEY = 'todo_account:frozen'


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
            TodoAccount.objects.select_for_update(of=('self',))
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
                    description=f'To-do storage, day {account.billed_days + 1}',
                    source=SOURCE,
                )
            except InsufficientCreditsError:
                freeze_account(account)
                return

        TodoAccount.objects.filter(pk=account.pk).update(
            accrued=remainder, last_billed_at=next_billed_at, billed_days=F('billed_days') + 1
        )


def freeze_account(account: TodoAccount) -> None:
    """Freeze the account (storage unpaid) and tell its owner, by email and in the app."""
    now = timezone.now()
    frozen = TodoAccount.objects.filter(pk=account.pk, frozen_at__isnull=True).update(frozen_at=now)
    account.frozen_at = now
    if frozen:
        enqueue_after_commit(send_storage_paused_email, str(account.user_id))
        _notify_storage_paused(account.user_id)


def _notify_storage_paused(user_id: UUID) -> None:
    """The in-app twin of the "storage paused" email (issue #169). Written in the caller's
    transaction, so it exists exactly when the freeze does; a failure to write it is logged and
    must not undo the freeze or the metering run. The body runs in its own savepoint so that
    nothing it does can abort the surrounding transaction, whatever it is."""
    try:
        with transaction.atomic():
            notifications.notify(
                user_id,
                source=SOURCE,
                title='Your to-do file storage is paused',
                body=(
                    "Your credit balance couldn't cover the storage for your attachments. "
                    'Nothing was deleted; uploads and downloads are paused until you add credits.'
                ),
                url=reverse('todo:settings'),
                key=FROZEN_NOTICE_KEY,
            )
    except Exception:
        logger.exception('Could not notify user %s that to-do storage is paused', user_id)


def _clear_storage_paused_notice(user_id: UUID) -> None:
    """Delete the "storage paused" notice once the account is unfrozen: it is stale. Same
    guarantees as the notice's creation: in the caller's transaction, in a savepoint of its own,
    logged rather than raised, so it can never undo or fail the resume."""
    try:
        with transaction.atomic():
            notifications.delete_for_key(user_id, FROZEN_NOTICE_KEY, source=SOURCE)
    except Exception:
        logger.exception('Could not clear the storage paused notice of user %s', user_id)


def unfreeze_account(account: TodoAccount) -> bool:
    """Unfreeze and restart the billing clock, so the frozen stretch is never billed. Returns
    whether this call did it. Also deletes the in-app "storage paused" notice, in the same
    transaction."""
    now = timezone.now()
    with transaction.atomic():
        won = TodoAccount.objects.filter(pk=account.pk, frozen_at__isnull=False).update(
            frozen_at=None, last_billed_at=now
        )
        if won:
            _clear_storage_paused_notice(account.user_id)
    if won:
        account.frozen_at = None
        account.last_billed_at = now
    return bool(won)


@task(queue_name='default')
def resume_account(user_id: str) -> None:
    resume(user_id)


def resume(user_id: str) -> bool:
    """Unfreeze the user's account if their balance covers a day of its storage. Returns whether
    it was unfrozen by this call. Does nothing for a user without a To-do account or one that
    isn't frozen."""
    try:
        user = User.objects.get(pk=UUID(user_id))
    except User.DoesNotExist, ValueError:
        return False
    account = TodoAccount.objects.filter(user=user).first()
    if account is None or account.frozen_at is None:
        return False
    if get_balance(user) < daily_storage_cost(account.stored_bytes):
        return False
    return unfreeze_account(account)


def on_credits_added(user: User) -> None:
    """The `billing.credits_added` receiver's work: queue the resume for a user whose storage is
    frozen. Only queues (the task runs after the commit, with the credits visible)."""
    if TodoAccount.objects.filter(user=user, frozen_at__isnull=False).exists():
        enqueue_after_commit(resume_account, str(user.pk))
