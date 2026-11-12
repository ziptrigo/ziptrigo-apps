"""A user's Notes account: creating it, and the lock every note write takes first."""

from django.db import IntegrityError, transaction

from apps.accounts.models import User

from ..models import NotesAccount


def get_account(user: User) -> NotesAccount:
    """The user's `NotesAccount`, created on first use (re-read when a concurrent first visit won
    the insert, like `load_singleton`). `account.user` is the `user` passed in, already loaded."""
    account = NotesAccount.objects.filter(user=user).first()
    if account is None:
        try:
            with transaction.atomic():
                account = NotesAccount.objects.create(user=user)
        except IntegrityError:
            account = NotesAccount.objects.get(user=user)
    account.user = user
    return account


def lock_account(user_id: object) -> None:
    """Lock the user's `NotesAccount` row (`select_for_update`), held until the caller's
    transaction ends. Call it inside a transaction as the **first** lock it takes: every write that
    counts notes or free slots (creating, restoring, trashing, saving) takes this one lock before
    the note's own row, so one user's writes take turns and can't both take the last free slot.
    The row must exist (`get_account` first); a second lock of it by the same transaction doesn't
    block."""
    NotesAccount.objects.select_for_update().filter(pk=user_id).first()
