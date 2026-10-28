"""What a task costs, charged through `apps.billing.services` (issue #178).

The first `TodoSettings.free_tasks_per_month` tasks a user creates in a calendar month (in their
time zone) are free, then each costs `task_price` credits. Subtasks and every occurrence of a
repeating task count, from any source. There are no refunds and no debt:

* creating a task the user asked for either pays or is refused (`InsufficientCreditsError`, and
  everything the creation did, the number and the month count included, rolls back);
* a task the *user did not ask for* (the next occurrence of a repeating task, when they complete
  one) is created anyway, **unpaid** (`Task.unpaid_at`), and new task creation is blocked
  (`TodoAccount.creation_blocked_at`) until a top-up settles it: `billing.credits_added` ->
  `on_credits_added` -> `settle_unpaid_tasks`, oldest first, stopping at the first the balance
  can't cover.

`charge_task_creation` and `accounts.allocate_number` run in the same locked transaction (the
account row), which `tasks.create_task` opens.
"""

import logging
from uuid import UUID

from django.db import transaction
from django.tasks import task
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today
from apps.billing.services import InsufficientCreditsError, get_balance, spend_credits
from apps.core.services.queue import enqueue_after_commit

from ..models import Task, TodoAccount, TodoSettings
from . import metering

logger = logging.getLogger(__name__)

SOURCE = 'todo'


def ledger_description(number: int) -> str:
    return f'To-do: task #{number}'


def month_key(user: User) -> str:
    """`YYYY-MM` of the user's current month, in their time zone."""
    return user_today(user).strftime('%Y-%m')


def charge_task_creation(
    account: TodoAccount, settings: TodoSettings, *, number: int, allow_unpaid: bool
) -> tuple[int, bool]:
    """Count one more task in the user's month and charge it when it is beyond the free ones.

    Returns `(credits_charged, unpaid)`. Call it in the transaction that holds the account row
    lock (`accounts.allocate_number` took it and refreshed `account`). On
    `InsufficientCreditsError` it re-raises unless `allow_unpaid`, in which case the price is
    returned as owed (`unpaid=True`) and `creation_blocked_at` is set; the caller records both on
    the task.
    """
    key = month_key(account.user)
    if account.month_key != key:
        account.month_key = key
        account.month_task_count = 0
    account.month_task_count += 1
    charged, unpaid = 0, False
    if account.month_task_count > settings.free_tasks_per_month and settings.task_price > 0:
        charged = settings.task_price
        try:
            with transaction.atomic():
                spend_credits(
                    account.user,
                    charged,
                    description=ledger_description(number),
                    source=SOURCE,
                )
        except InsufficientCreditsError:
            if not allow_unpaid:
                raise
            unpaid = True
            if account.creation_blocked_at is None:
                account.creation_blocked_at = timezone.now()
    account.save(update_fields=['month_key', 'month_task_count', 'creation_blocked_at'])
    return charged, unpaid


def month_usage(account: TodoAccount, settings: TodoSettings) -> tuple[int, int]:
    """`(used, free)` for the user's current month: `0` used when the counted month is stale."""
    used = account.month_task_count if account.month_key == month_key(account.user) else 0
    return used, settings.free_tasks_per_month


def cannot_afford(account: TodoAccount, settings: TodoSettings) -> bool:
    """A cheap look before something slow or costly (the AI call, an upload, a rate-limit hit): the
    next task is beyond the free allowance and the balance can't pay its price. Takes no lock, so
    the real decision stays `charge_task_creation`'s, under the account lock."""
    if settings.task_price <= 0:
        return False
    used, free = month_usage(account, settings)
    return used >= free and get_balance(account.user) < settings.task_price


@task(queue_name='default')
def settle_unpaid_tasks(user_id: str) -> None:
    settle(user_id)


def settle(user_id: str) -> int:
    """Pay the user's unpaid tasks, oldest first, each in its own transaction, stopping at the
    first the balance can't cover. When none remain, task creation is unblocked. Idempotent.
    Returns how many were settled."""
    try:
        user = User.objects.get(pk=UUID(user_id))
    except User.DoesNotExist, ValueError:
        return 0
    settled = 0
    for task_id in list(
        Task.objects.filter(user=user, unpaid_at__isnull=False)
        .order_by('created_at', 'number')
        .values_list('pk', flat=True)
    ):
        try:
            with transaction.atomic():
                unpaid = (
                    Task.objects.select_for_update()
                    .filter(pk=task_id, unpaid_at__isnull=False)
                    .first()
                )
                if unpaid is None:  # settled meanwhile, or deleted
                    continue
                if unpaid.charged_credits > 0:
                    spend_credits(
                        user,
                        unpaid.charged_credits,
                        description=ledger_description(unpaid.number),
                        source=SOURCE,
                    )
                Task.objects.filter(pk=unpaid.pk).update(unpaid_at=None)
                settled += 1
        except InsufficientCreditsError:
            break
    _unblock_if_settled(user)
    return settled


def _unblock_if_settled(user: User) -> None:
    """Clear `creation_blocked_at` when no unpaid task remains. The check and the write run in one
    transaction under the account row lock (the one `accounts.allocate_number` takes), so a
    concurrent `create_task(..., allow_unpaid=True)` either commits its unpaid task before the check
    (which then sees it) or waits and sets the block again after: it can never be cleared over an
    unpaid task."""
    with transaction.atomic():
        locked = TodoAccount.objects.select_for_update().filter(pk=user.pk).first()
        if locked is None or locked.creation_blocked_at is None:
            return
        if not Task.objects.filter(user=user, unpaid_at__isnull=False).exists():
            TodoAccount.objects.filter(pk=user.pk).update(creation_blocked_at=None)


def on_credits_added(user: User) -> None:
    """The `billing.credits_added` receiver's work: queue the settling for a user who owes
    something, and the resume of their attachment storage when it is frozen. Only queues (the
    tasks run after the commit, with the credits visible). Each is attempted on its own, so a
    failure queuing one never skips the other."""
    try:
        if TodoAccount.objects.filter(user=user, creation_blocked_at__isnull=False).exists():
            enqueue_after_commit(settle_unpaid_tasks, str(user.pk))
    except Exception:
        logger.exception('Could not queue settle_unpaid_tasks for user %s', user.pk)
    try:
        metering.on_credits_added(user)
    except Exception:
        logger.exception('Could not queue resume_account for user %s', user.pk)
