"""A user's World Time account: creating it, and the lock every counting write takes first."""

from django.db import IntegrityError, transaction

from apps.accounts.models import User

from ..models import WorldTimeAccount


def get_account(user: User) -> WorldTimeAccount:
    """The user's `WorldTimeAccount`, created on first use (re-read when a concurrent first visit
    won the insert). `account.user` is the `user` passed in, already loaded."""
    account = WorldTimeAccount.objects.filter(user=user).first()
    if account is None:
        try:
            with transaction.atomic():
                account = WorldTimeAccount.objects.create(user=user)
        except IntegrityError:
            account = WorldTimeAccount.objects.get(user=user)
    account.user = user
    return account


def lock_account(user_id: object) -> None:
    """Lock the user's `WorldTimeAccount` row (`select_for_update`) until the caller's transaction
    ends. Call it inside a transaction as the **first** lock it takes: every write that counts tabs
    or locations, or moves the home row, takes this one lock, so one user's writes take turns. The
    row must exist (`get_account` first)."""
    WorldTimeAccount.objects.select_for_update().filter(pk=user_id).first()
