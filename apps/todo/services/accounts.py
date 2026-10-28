"""A user's To-do account: creating it, and handing out task numbers."""

from datetime import time

from django.db import IntegrityError, transaction
from django.db.models import F

from apps.accounts.models import User

from ..models import TodoAccount


def get_account(user: User) -> TodoAccount:
    """The user's `TodoAccount`, created on first use (re-read when a concurrent first visit won
    the insert, like `load_singleton`)."""
    account = TodoAccount.objects.filter(user=user).first()
    if account is not None:
        return account
    try:
        with transaction.atomic():
            return TodoAccount.objects.create(user=user)
    except IntegrityError:
        return TodoAccount.objects.get(user=user)


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
