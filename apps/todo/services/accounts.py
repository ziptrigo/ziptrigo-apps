"""A user's To-do account: creating it, handing out task numbers, and the email address tasks can
be sent to (issue #186)."""

from collections.abc import Iterable
from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services.mail.addresses import parse_allowlist

from ..models import InboxCode, TodoAccount


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
    """Save the time a task with a date and no time is treated as due. When it changed, the unsent
    reminders of the user's date-only tasks (they are due at this time) are recomputed by a task
    queued on commit, like a time zone change: a user can have thousands of tasks, and this runs in
    the request."""
    from apps.core.services.queue import enqueue_after_commit

    from . import reminders  # `reminders` imports this module

    changed = account.default_due_time != due_time
    account.default_due_time = due_time
    with transaction.atomic():
        account.save(update_fields=['default_due_time'])
        if changed:
            enqueue_after_commit(reminders.reschedule_user_reminders, str(account.user_id), True)
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


# -- The inbox address (issue #186) ---------------------------------------------------------------


def active_inbox_code(account: TodoAccount) -> InboxCode:
    """The account's active code, issued on first use (an account created before the email intake
    existed has none yet)."""
    code = InboxCode.objects.filter(account=account, active=True).first()
    return code if code is not None else InboxCode.issue(account)


def inbox_address(code: str) -> str:
    """`todo+<code>@<domain>`, from `settings.TODO_INBOUND_ADDRESS` (`todo@ziptrigo.com`)."""
    local, _, domain = settings.TODO_INBOUND_ADDRESS.partition('@')
    local = local.split('+', 1)[0]
    return f'{local}+{code}@{domain}'


def inbox_address_for(user: User) -> str:
    return inbox_address(active_inbox_code(get_account(user)).code)


def regenerate_inbox_code(user: User) -> InboxCode:
    """Retire the active address immediately and issue a new one. The old code is kept forever
    and never reissued, so mail still sent to it is dropped rather than delivered to a stranger."""
    account = get_account(user)
    with transaction.atomic():
        InboxCode.objects.filter(account=account, active=True).update(
            active=False, retired_at=timezone.now()
        )
        return InboxCode.issue(account)


def update_inbox_settings(user: User, *, sender_allowlist: str | Iterable[str]) -> list[str]:
    """Set the accepted senders (a list, or the text the user typed; an empty one accepts anyone)
    and return the normalized list.

    Raises:
        ValidationError: an entry isn't an address or `*@domain`, or there are too many (keyed
            `sender_allowlist`).
    """
    account = get_account(user)
    try:
        allowlist = parse_allowlist(sender_allowlist)
    except ValidationError as exc:
        raise ValidationError({'sender_allowlist': list(exc.messages)}) from exc
    account.sender_allowlist = allowlist
    account.save(update_fields=['sender_allowlist'])
    return allowlist
