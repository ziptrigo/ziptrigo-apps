"""A user's To-do account: creating it, and handing out task numbers."""

from datetime import time

from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.accounts.models import User

from ..models import TodoAccount


def get_account(user: User) -> TodoAccount:
    """The user's `TodoAccount`, created on first use (re-read when a concurrent first visit won
    the insert, like `load_singleton`). `account.user` is the `user` passed in, already loaded, so
    billing and the pages that read it cost no extra query."""
    account = TodoAccount.objects.filter(user=user).first()
    if account is None:
        try:
            with transaction.atomic():
                account = TodoAccount.objects.create(user=user)
        except IntegrityError:
            account = TodoAccount.objects.get(user=user)
    account.user = user
    return account


def allocate_number(account: TodoAccount) -> int:
    """The next task number of `account`'s user, taken for good (numbers are never reused: a
    trashed or purged task keeps its number out of circulation).

    Must run inside the caller's transaction: it locks the account row (`select_for_update`) so two
    creations can't read the same number, and the lock is held until the caller commits, which is
    also what serialises the month count `billing.charge_task_creation` updates next. `account` is
    refreshed from the locked row, so what the caller then reads (`month_key`, ...) is current.
    """
    locked = TodoAccount.objects.select_for_update().get(pk=account.pk)
    number = locked.next_task_number
    TodoAccount.objects.filter(pk=account.pk).update(next_task_number=F('next_task_number') + 1)
    account.next_task_number = number + 1
    account.month_key = locked.month_key
    account.month_task_count = locked.month_task_count
    account.creation_blocked_at = locked.creation_blocked_at
    return number


def update_default_due_time(account: TodoAccount, due_time: time) -> TodoAccount:
    """Save the time a task with a date and no time is treated as due."""
    account.default_due_time = due_time
    account.save(update_fields=['default_due_time'])
    return account


def add_stored_bytes(user_id: object, delta: int) -> None:
    """Add `delta` (negative to remove) to the account's `stored_bytes`, never going below zero.

    Every attachment that lands in or leaves the bucket goes through here, so billing sees exactly
    what is stored. When an empty account gets its first bytes the billing clock restarts, so the
    empty stretch before is never billed. (The twin of `receipts.services.accounts`' function.)
    """
    if not delta:
        return
    if delta > 0:
        TodoAccount.objects.filter(pk=user_id, stored_bytes=0).update(last_billed_at=timezone.now())
    TodoAccount.objects.filter(pk=user_id).update(
        stored_bytes=Greatest(F('stored_bytes') + delta, Value(0))
    )


def is_frozen(user_id: object) -> bool:
    """Whether the user's attachment storage is paused for lack of credits."""
    return TodoAccount.objects.filter(pk=user_id, frozen_at__isnull=False).exists()
