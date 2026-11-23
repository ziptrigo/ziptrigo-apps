"""Helpers for the credit usage report's tests (issue #347)."""

from datetime import UTC, date, datetime
from typing import cast

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.billing.models import CreditTransaction, CreditTransactionType


def ledger(user, amount, *, kind=None, source='', at=None, description=''):
    """One ledger row of `user` at the instant `at` (UTC; a datetime), straight into the table:
    the report reads rows, whatever wrote them. A negative amount is a spend by default."""
    if kind is None:
        kind = CreditTransactionType.SPEND if amount < 0 else CreditTransactionType.PURCHASE
    row = CreditTransaction.objects.create(
        user=user, amount=amount, type=kind, source=source, description=description
    )
    if at is not None:
        CreditTransaction.objects.filter(pk=row.pk).update(created_at=at)
    return row


def spend(user, credits, source, at):
    return ledger(user, -credits, kind=CreditTransactionType.SPEND, source=source, at=at)


def refund(user, credits, source, at):
    return ledger(user, credits, kind=CreditTransactionType.REFUND, source=source, at=at)


def utc(year, month, day, hour=12, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def day(year, month, number):
    return date(year, month, number)


def make_user(**fields) -> User:
    """A user from the factory, typed as the model for the type checker."""
    return cast(User, UserFactory(**fields))
