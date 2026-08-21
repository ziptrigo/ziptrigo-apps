from django.conf import settings
from django.db import models


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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='credit_transactions',
    )
    amount = models.IntegerField(help_text='Credits added (positive) or spent (negative).')
    type = models.CharField(max_length=32, choices=CreditTransactionType.choices)
    description = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='Human-readable notes describing why this transaction happened.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.user_id} {self.type} {self.amount} @ {self.created_at.isoformat()}'
