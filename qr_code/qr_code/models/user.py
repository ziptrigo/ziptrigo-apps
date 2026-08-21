from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.db.models import F

from .credit_transaction import CreditTransaction


class InsufficientCreditsError(Exception):
    """Raised when attempting to spend more credits than are available."""


class User(AbstractUser):
    """Custom user model with additional fields."""

    name = models.CharField(max_length=255, help_text='User full name')
    email = models.EmailField(unique=True, help_text='User email address')
    email_confirmed = models.BooleanField(default=False, help_text='Whether email is confirmed')
    email_confirmed_at = models.DateTimeField(
        null=True, blank=True, help_text='When email was confirmed'
    )

    # Credits system
    # - ``credits`` stores the current balance.
    # - ``CreditTransaction`` stores the immutable history (ledger).
    credits = models.PositiveBigIntegerField(
        default=0,
        help_text='Current credits balance.',
    )

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def get_credit_balance(self) -> int:
        """Return the current credit balance.

        Note: this returns the value currently loaded on the instance; call
        ``refresh_from_db(fields=['credits'])`` if you need the latest value.
        """
        return int(self.credits)

    def add_credits(self, amount: int, tx_type: str = 'purchase', description: str = '') -> None:
        """Add credits to the user's balance and record a transaction.

        Args:
            amount: Number of credits to add. Must be > 0.
            tx_type: Transaction type (e.g. 'purchase', 'adjustment').
            description: Human-readable description for the ledger.
        """
        if amount <= 0:
            raise ValueError('amount must be > 0')

        model = type(self)
        with transaction.atomic():
            model.objects.filter(pk=self.pk).update(credits=F('credits') + amount)
            CreditTransaction.objects.create(
                user=self,
                amount=amount,
                type=tx_type,
                description=description,
            )

        self.refresh_from_db(fields=['credits'])

    def spend_credits(self, amount: int, tx_type: str = 'spend', description: str = '') -> None:
        """Spend credits from the user's balance and record a transaction.

        This method is race-condition safe: it uses an atomic conditional update
        that prevents the balance from going below zero.

        Args:
            amount: Number of credits to spend. Must be > 0.
            tx_type: Transaction type (e.g. 'spend', 'adjustment').
            description: Human-readable description for the ledger.

        Raises:
            InsufficientCreditsError: If the user doesn't have enough credits.
        """
        if amount <= 0:
            raise ValueError('amount must be > 0')

        model = type(self)
        with transaction.atomic():
            updated = model.objects.filter(pk=self.pk, credits__gte=amount).update(
                credits=F('credits') - amount
            )
            if updated != 1:
                raise InsufficientCreditsError('Insufficient credits')

            CreditTransaction.objects.create(
                user=self,
                amount=-amount,
                type=tx_type,
                description=description,
            )

        self.refresh_from_db(fields=['credits'])

    def __str__(self) -> str:
        return f'{self.email} - {self.name}'
