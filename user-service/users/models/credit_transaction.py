import builtins
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

if TYPE_CHECKING:
    from django.db.models import Manager


class CreditTransactionType(models.TextChoices):
    """Supported credit transaction types."""

    PURCHASE = 'purchase', 'Purchase'
    SPEND = 'spend', 'Spend'
    ADJUSTMENT = 'adjustment', 'Adjustment'
    REFUND = 'refund', 'Refund'


class CreditTransaction(models.Model):
    """Ledger of credit changes for a user.

    Notes:
        - ``User.credits`` stores the current balance.
        - ``CreditTransaction`` stores the immutable history of changes.
    """

    # Not explicitly assigned -- Django's `ModelBase` metaclass injects both of these on every
    # concrete model that doesn't declare its own; see the matching comment in
    # `users/models/user.py`. `DoesNotExist` spells out `builtins.type` because the `type` field
    # below shadows the bare `type` builtin for annotation lookups anywhere in this class body.
    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[builtins.type[ObjectDoesNotExist]]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='credit_transactions',
    )
    # Django adds this `_id` companion attribute automatically for every `ForeignKey`; it's never
    # declared as a field itself, so `ty` has no way to know it exists without this annotation.
    user_id: UUID
    amount = models.IntegerField(help_text='Credits added (positive) or spent (negative).')
    type = models.CharField(max_length=32, choices=CreditTransactionType.choices)
    description = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='Human-readable notes describing why this transaction happened.',
    )
    # See the matching comment in `users/models/user.py` for why this is `cast` instead of left as
    # the declared `models.DateTimeField`.
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.user_id} {self.type} {self.amount} @ {self.created_at.isoformat()}'
