from decimal import Decimal
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models

_GB = 1024**3


class FileTransferSettings(models.Model):
    """Singleton admin-editable settings for the app (spec section 9): logged-in and anonymous
    limits, the anonymous on/off switch, allowed anonymous expiry values, metering price and the
    suspension grace period. Always the row with `pk=1`; use `FileTransferSettings.load()`.

    Anonymous sending is out of scope for phase 1 (see `CLAUDE.md` known gaps), but its fields are
    included now (per the issue) to avoid a second migration later.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    # Logged-in limits.
    logged_in_max_file_size_bytes = cast(
        int, models.PositiveBigIntegerField(default=5 * _GB, verbose_name='Max file size (bytes)')
    )
    logged_in_max_files = cast(int, models.PositiveIntegerField(default=100))
    logged_in_max_total_size_bytes = cast(
        int,
        models.PositiveBigIntegerField(default=20 * _GB, verbose_name='Max total size (bytes)'),
    )
    logged_in_max_recipients = cast(int, models.PositiveIntegerField(default=20))

    # Anonymous limits (phase 2).
    anonymous_enabled = cast(bool, models.BooleanField(default=False))
    anonymous_max_file_size_bytes = cast(int, models.PositiveBigIntegerField(default=2 * _GB))
    anonymous_max_files = cast(int, models.PositiveIntegerField(default=20))
    anonymous_max_total_size_bytes = cast(int, models.PositiveBigIntegerField(default=5 * _GB))
    anonymous_max_recipients = cast(int, models.PositiveIntegerField(default=10))
    anonymous_max_transfers_per_ip_per_day = cast(int, models.PositiveIntegerField(default=5))
    anonymous_max_bytes_per_ip_per_day = cast(int, models.PositiveBigIntegerField(default=5 * _GB))
    anonymous_allowed_expiry_days = cast(
        str,
        models.CharField(
            max_length=64,
            default='1,5,15,30',
            help_text='Comma-separated allowed expiry values (in days) offered to anonymous '
            'senders.',
        ),
    )

    # Metering (spec section 7).
    price_per_gb_per_day = cast(
        Decimal,
        models.DecimalField(
            max_digits=10,
            decimal_places=4,
            default=Decimal('0.1000'),
            help_text='Credits charged per GB stored per day, for logged-in senders.',
        ),
    )
    suspension_grace_days = cast(
        int,
        models.PositiveIntegerField(
            default=7,
            help_text='Days a suspended (out-of-credits) transfer is kept before its files are '
            'deleted, if not topped up.',
        ),
    )

    class Meta:
        verbose_name = 'File transfer settings'
        verbose_name_plural = 'File transfer settings'

    def __str__(self) -> str:
        return 'File transfer settings'

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'FileTransferSettings':
        """Return the singleton row, creating it with defaults on first use."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def anonymous_allowed_expiry_days_list(self) -> list[int]:
        return [
            int(value.strip())
            for value in self.anonymous_allowed_expiry_days.split(',')
            if value.strip()
        ]
