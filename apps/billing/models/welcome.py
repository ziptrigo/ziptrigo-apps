import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

if TYPE_CHECKING:
    from django.db.models import Manager


class WelcomeCreditStatus(models.TextChoices):
    #: Opened at account creation; waits for the email to be confirmed.
    PENDING = 'pending', 'Pending'
    GRANTED = 'granted', 'Granted'
    REFUSED = 'refused', 'Refused'


class WelcomeRefusal(models.TextChoices):
    #: The address, or a `+tag` / Gmail-dots variant of it, was already confirmed on an account.
    ADDRESS_SEEN = 'address_seen', 'Address already used'


class WelcomeCredit(models.Model):
    """The welcome credits of one new account (issue #160): at most one row per user, which is the
    once-per-user guard. Opened `pending` when the account is created (only while
    `BillingSettings.welcome_credits` is above 0, and not for staff), then `granted` or `refused`
    once the email is confirmed. Accounts that existed before the feature, and accounts created
    while it was off, have no row and never get credits. Rules in `services.welcome`."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='welcome_credit'
    )
    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=WelcomeCreditStatus.choices,
            default=WelcomeCreditStatus.PENDING,
        ),
    )
    reason = cast(
        str,
        models.CharField(max_length=32, choices=WelcomeRefusal.choices, blank=True, default=''),
    )
    credits = cast(int, models.PositiveIntegerField(default=0, help_text='Credits granted.'))
    transaction = models.ForeignKey(
        'billing.CreditTransaction',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
    )
    transaction_id: int | None
    user_id: uuid.UUID
    #: Set, once, by the task that sends the "no welcome credits" email: the guard for one email
    #: per account.
    refusal_emailed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    decided_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Welcome credit'
        verbose_name_plural = 'Welcome credits'

    def __str__(self) -> str:
        return f'{self.user_id}: {self.status}'
