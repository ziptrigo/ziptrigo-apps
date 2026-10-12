from datetime import datetime
from decimal import Decimal
from typing import ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User


class ReceiptsAccount(models.Model):
    """A user's Receipts account: their inbox settings and the storage-billing state. Created
    lazily on first use, through `apps.receipts.services.accounts.get_account`, which also seeds
    the default categories and the first inbox code.

    Storage is billed per account (`services.metering`): `stored_bytes` x the shared storage
    price, once a day. `frozen_at` is set when the balance can't cover it; nothing is deleted while
    frozen, but previews, downloads and processing are refused, and the frozen days are never
    billed afterwards (unfreezing restarts the clock).
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    user = cast(
        'User',
        models.OneToOneField(
            settings.AUTH_USER_MODEL,
            on_delete=models.CASCADE,
            primary_key=True,
            related_name='receipts_account',
        ),
    )
    user_id: UUID

    default_currency = cast(str, models.CharField(max_length=3, default='USD'))
    sender_allowlist = cast(
        list[str],
        models.JSONField(
            default=list,
            blank=True,
            help_text='Accepted senders for email intake: exact addresses and `*@domain` '
            'wildcards, lowercase. Empty accepts anyone.',
        ),
    )

    stored_bytes = cast(
        int,
        models.PositiveBigIntegerField(
            default=0,
            help_text='Everything stored for this account and billed: original files, previews '
            'and stripped emails, trashed ones included until they are purged.',
        ),
    )
    last_billed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    accrued = cast(
        Decimal,
        models.DecimalField(
            max_digits=14,
            decimal_places=6,
            default=Decimal('0'),
            help_text='Fractional credits owed but not yet spent (billing.spend_credits only '
            'takes whole credits); see apps.receipts.services.metering.',
        ),
    )
    billed_days = cast(int, models.PositiveIntegerField(default=0))
    frozen_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='Set when storage could not be paid for; cleared on top-up.',
        ),
    )

    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))

    class Meta:
        verbose_name = 'Receipts account'

    def __str__(self) -> str:
        return f'Receipts account of {self.user_id}'

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None
