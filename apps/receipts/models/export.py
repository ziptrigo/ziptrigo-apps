"""Exports (issue #315): a user's receipt files, packed into independent zip files kept in S3 for a
while. `ReceiptExport` is one request; its `ReceiptExportPart`s are the zip files; its
`ReceiptExportItem`s are what goes into them (the plan, and what the CSV is written from). Items
and parts are deleted when the export ends (expired, failed or deleted), so the tables stay small.

Every new column has a `db_default` (the container still serving during the migration doesn't know
them).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

if TYPE_CHECKING:
    from django.db.models import Manager


class ExportStatus(models.TextChoices):
    """`BUILDING` -> `READY` | `FAILED`; `READY` -> `EXPIRED` (after the retention days) or
    `DELETED` (the user deleted it). A failed one is refunded in full."""

    BUILDING = 'building', 'Preparing'
    READY = 'ready', 'Ready'
    FAILED = 'failed', 'Failed'
    EXPIRED = 'expired', 'Expired'
    DELETED = 'deleted', 'Deleted'


class ExportLayout(models.TextChoices):
    YEAR_VENDOR = 'year_vendor', 'Year / Vendor'
    VENDOR_YEAR = 'vendor_year', 'Vendor / Year'


class ReceiptExport(models.Model):
    """One "download my files" request. Charged in full when it is created (`charged_credits`,
    at `price_per_file` each); a failed build refunds all of it, a file that had vanished by the
    time it was zipped refunds its own share (`refunded_credits`). The status change that does a
    refund is the compare-and-swap that makes it happen once."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    # The reverse accessors (`related_name`), declared for the type checker.
    parts: ClassVar['Manager']
    items: ClassVar['Manager']

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='receipt_exports'
        ),
    )
    user_id: uuid.UUID
    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=ExportStatus.choices,
            default=ExportStatus.BUILDING,
            db_default=ExportStatus.BUILDING,
        ),
    )
    layout = cast(
        str,
        models.CharField(
            max_length=16,
            choices=ExportLayout.choices,
            default=ExportLayout.YEAR_VENDOR,
            db_default=ExportLayout.YEAR_VENDOR,
        ),
    )
    filters = cast(
        dict,
        models.JSONField(
            default=dict,
            db_default={},
            blank=True,
            help_text='What was listed when the export was asked for (`summary`: lines for '
            'display, `params`: the dashboard query).',
        ),
    )
    file_count = cast(int, models.PositiveIntegerField(default=0, db_default=0))
    total_bytes = cast(int, models.PositiveBigIntegerField(default=0, db_default=0))
    part_count = cast(int, models.PositiveIntegerField(default=0, db_default=0))
    price_per_file = cast(
        int,
        models.PositiveIntegerField(
            default=0, db_default=0, help_text='The price per file when it was charged.'
        ),
    )
    charged_credits = cast(int, models.PositiveIntegerField(default=0, db_default=0))
    refunded_credits = cast(int, models.PositiveIntegerField(default=0, db_default=0))
    skipped_count = cast(
        int,
        models.PositiveIntegerField(
            default=0,
            db_default=0,
            help_text='Files that were gone when they were zipped (left out and refunded).',
        ),
    )
    attempts = cast(
        int,
        models.PositiveSmallIntegerField(
            default=0, db_default=0, help_text='Failed tries of the part being built.'
        ),
    )
    progress_at = cast(
        datetime,
        models.DateTimeField(
            auto_now_add=True,
            help_text='When the build last made progress; `receipts.retry_due` re-queues one '
            'that has not for a while.',
        ),
    )
    lease_until = cast(
        'datetime | None',
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='A worker is building a part until this time (or a retry waits for it).',
        ),
    )
    ready_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    expires_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    last_error = cast(str, models.CharField(max_length=255, blank=True, default='', db_default=''))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'expires_at']),
        ]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'Export {self.pk} ({self.status})'

    @property
    def is_building(self) -> bool:
        return self.status == ExportStatus.BUILDING

    @property
    def is_ready(self) -> bool:
        return self.status == ExportStatus.READY


class ReceiptExportPart(models.Model):
    """One zip file of an export. `built_at` is null until it is written."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.BigAutoField(primary_key=True)
    export = cast(
        'ReceiptExport',
        models.ForeignKey(ReceiptExport, on_delete=models.CASCADE, related_name='parts'),
    )
    export_id: uuid.UUID
    number = cast(int, models.PositiveIntegerField())
    key = cast(str, models.CharField(max_length=512))
    size_bytes = cast(int, models.PositiveBigIntegerField(default=0, db_default=0))
    file_count = cast(int, models.PositiveIntegerField(default=0, db_default=0))
    built_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['export', 'number'], name='receipts_export_part_unique')
        ]
        ordering = ['number']

    def __str__(self) -> str:
        return f'Export {self.export_id}, part {self.number}'


class ReceiptExportItem(models.Model):
    """One receipt file in an export, with the data its CSV line is written from (a copy, taken
    when the export was planned, so a later edit or delete of the receipt changes nothing).
    `receipt_id` is a plain UUID, not a foreign key: a receipt deleted for good meanwhile must not
    break the export (its file is then simply `skipped`)."""

    objects: ClassVar['Manager']

    id = models.BigAutoField(primary_key=True)
    export = cast(
        'ReceiptExport',
        models.ForeignKey(ReceiptExport, on_delete=models.CASCADE, related_name='items'),
    )
    export_id: uuid.UUID
    part_number = cast(int, models.PositiveIntegerField())
    receipt_id = cast(uuid.UUID, models.UUIDField())
    file_key = cast(str, models.CharField(max_length=512))
    path = cast(str, models.CharField(max_length=512, help_text='The name inside the zip.'))
    size_bytes = cast(int, models.PositiveBigIntegerField(default=0, db_default=0))
    skipped = cast(bool, models.BooleanField(default=False, db_default=False))
    receipt_date = cast('date | None', models.DateField(null=True, blank=True))
    vendor = cast(str, models.CharField(max_length=255, blank=True, default='', db_default=''))
    document_type = cast(
        str, models.CharField(max_length=16, blank=True, default='', db_default='')
    )
    amount = cast(
        'Decimal | None',
        models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True),
    )
    currency = cast(str, models.CharField(max_length=3, blank=True, default='', db_default=''))

    class Meta:
        indexes = [models.Index(fields=['export', 'part_number'])]
        ordering = ['part_number', 'path']

    def __str__(self) -> str:
        return self.path
