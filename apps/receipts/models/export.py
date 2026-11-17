"""Exports (issue #315): a user's receipt files, packed into independent zip files kept in S3 for a
while. `ReceiptExport` is one request; its `ReceiptExportPart`s are the zip files; its
`ReceiptExportItem`s are what goes into them (the plan, and what the CSV is written from). Items
and parts are deleted when the export ends (expired, failed or deleted), so the tables stay small.

The columns every product's export has are the abstract bases in `apps.core.models.exports` (issue
#326); what is Receipts' own is here: the owner, the folder layout, what was listed, and the copy
of a receipt's CSV line on an item.

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
from apps.core.models.exports import BaseExport, BaseExportItem, BaseExportPart, ExportStatus

if TYPE_CHECKING:
    from django.db.models import Manager

__all__ = [
    'ExportLayout',
    'ExportStatus',
    'ReceiptExport',
    'ReceiptExportItem',
    'ReceiptExportPart',
]


class ExportLayout(models.TextChoices):
    YEAR_VENDOR = 'year_vendor', 'Year / Vendor'
    VENDOR_YEAR = 'vendor_year', 'Vendor / Year'


class ReceiptExport(BaseExport):
    """One "download my files" request (the shared lifecycle columns are `BaseExport`'s)."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    # The reverse accessors (`related_name`), declared for the type checker.
    parts: ClassVar['Manager']
    items: ClassVar['Manager']

    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='receipt_exports'
        ),
    )
    user_id: uuid.UUID
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
    progress_at = cast(
        datetime,
        models.DateTimeField(
            auto_now_add=True,
            help_text='When the build last made progress; `receipts.retry_due` re-queues one '
            'that has not for a while.',
        ),
    )

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'expires_at']),
        ]
        constraints = [
            # One export being prepared per user, whatever races: what lets `create_export` take no
            # lock on the account while it writes the (possibly huge) plan.
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(status='building'),
                name='receipts_export_one_building_per_user',
            )
        ]
        ordering = ['-created_at']


class ReceiptExportPart(BaseExportPart):
    """One zip file of an export. `built_at` is null until it is written."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    export = cast(
        'ReceiptExport',
        models.ForeignKey(ReceiptExport, on_delete=models.CASCADE, related_name='parts'),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['export', 'number'], name='receipts_export_part_unique')
        ]
        ordering = ['number']


class ReceiptExportItem(BaseExportItem):
    """One receipt file in an export, with the data its CSV line is written from (a copy, taken
    when the export was planned, so a later edit or delete of the receipt changes nothing).
    `receipt_id` is a plain UUID, not a foreign key: a receipt deleted for good meanwhile must not
    break the export (its file is then simply `skipped`)."""

    objects: ClassVar['Manager']

    export = cast(
        'ReceiptExport',
        models.ForeignKey(ReceiptExport, on_delete=models.CASCADE, related_name='items'),
    )
    receipt_id = cast(uuid.UUID, models.UUIDField())
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
