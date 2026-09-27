"""Credit balance operations.

This is the only way any app should change a user's credits: every change updates the
`CreditAccount` balance and appends a `CreditTransaction` to the ledger in one transaction.
Product apps call these with ``source=<their app label>`` so the ledger shows where credits went.
"""

from django.db import transaction
from django.db.models import F

from apps.accounts.models import User

from ..models import CreditAccount, CreditTransaction, CreditTransactionType
from ..signals import credits_added


class InsufficientCreditsError(Exception):
    """Raised when attempting to spend more credits than are available."""


def get_balance(user: User) -> int:
    """Return the user's current credit balance."""
    account = CreditAccount.objects.filter(user=user).only('balance').first()
    return account.balance if account else 0


def add_credits(
    user: User,
    amount: int,
    *,
    tx_type: str = CreditTransactionType.PURCHASE,
    description: str = '',
    source: str = '',
) -> CreditTransaction:
    """Add credits to the user's balance and record a transaction.

    Args:
        user: The user whose balance changes.
        amount: Number of credits to add. Must be > 0.
        tx_type: Transaction type (a `CreditTransactionType` value).
        description: Human-readable description for the ledger.
        source: Label of the app making the change.
    """
    if amount <= 0:
        raise ValueError('amount must be > 0')

    with transaction.atomic():
        CreditAccount.objects.get_or_create(user=user)
        CreditAccount.objects.filter(user=user).update(balance=F('balance') + amount)
        tx = CreditTransaction.objects.create(
            user=user,
            amount=amount,
            type=tx_type,
            description=description,
            source=source,
        )
        # Deferred to commit: a receiver (e.g. file_transfer re-enabling suspended transfers) that
        # reads the balance must see this change, and a rolled-back transaction must not fire it.
        transaction.on_commit(lambda: credits_added.send(sender=None, user=user, amount=amount))
        return tx


def spend_credits(
    user: User,
    amount: int,
    *,
    tx_type: str = CreditTransactionType.SPEND,
    description: str = '',
    source: str = '',
) -> CreditTransaction:
    """Spend credits from the user's balance and record a transaction.

    Race-condition safe: an atomic conditional update keeps the balance from going below zero.

    Args:
        user: The user whose balance changes.
        amount: Number of credits to spend. Must be > 0.
        tx_type: Transaction type (a `CreditTransactionType` value).
        description: Human-readable description for the ledger.
        source: Label of the app making the change.

    Raises:
        InsufficientCreditsError: If the user doesn't have enough credits.
    """
    if amount <= 0:
        raise ValueError('amount must be > 0')

    with transaction.atomic():
        updated = CreditAccount.objects.filter(user=user, balance__gte=amount).update(
            balance=F('balance') - amount
        )
        if updated != 1:
            raise InsufficientCreditsError('Insufficient credits')

        return CreditTransaction.objects.create(
            user=user,
            amount=-amount,
            type=tx_type,
            description=description,
            source=source,
        )


def apply_credits(
    user: User,
    amount: int,
    *,
    tx_type: str,
    description: str = '',
    source: str = '',
) -> CreditTransaction:
    """Add (``amount > 0``) or spend (``amount < 0``) credits, e.g. for admin adjustments.

    Raises:
        InsufficientCreditsError: If ``amount`` is negative and the user doesn't have enough.
    """
    if amount > 0:
        return add_credits(user, amount, tx_type=tx_type, description=description, source=source)
    if amount < 0:
        return spend_credits(user, -amount, tx_type=tx_type, description=description, source=source)
    raise ValueError('amount must not be 0')
