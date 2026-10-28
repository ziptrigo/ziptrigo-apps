from decimal import Decimal
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.singleton import load_singleton


class BillingSettings(models.Model):
    """Singleton admin-editable settings shared by every product that charges credits: today just
    the storage price (issue #98), which file transfer and receipts both bill against. Always the
    row with `pk=1`; use `BillingSettings.load()`. Mirrors
    `apps.file_transfer.models.FileTransferSettings`.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: int

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    storage_price_per_gb_per_day = cast(
        Decimal,
        models.DecimalField(
            max_digits=10,
            decimal_places=4,
            default=Decimal('0.1000'),
            validators=[MinValueValidator(Decimal('0'))],
            verbose_name='Storage price (credits per GB per day)',
            help_text='Credits charged per GB stored per day, for every product that stores '
            'files for a logged-in user (file transfer, receipts).',
        ),
    )

    class Meta:
        verbose_name = 'Billing settings'
        verbose_name_plural = 'Billing settings'

    def __str__(self) -> str:
        return 'Billing settings'

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'BillingSettings':
        """Return the singleton row, creating it with defaults on first use."""
        return load_singleton(cls)
