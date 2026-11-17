"""The columns every product's export tables share (issue #315, moved here for the To-do export,
issue #326): abstract bases a product subclasses with its own `user` foreign key, the `export`
foreign key of its parts and items, and whatever else is its own (Receipts' `layout`, the copy of
a receipt's CSV line on an item, To-do's copy of a task's ids).

An export is one "download my files" request (`BaseExport`); its parts are the zip files in S3
(`BaseExportPart`); its items are what goes into them, the plan (`BaseExportItem`). Items and
parts are deleted when the export ends (expired, failed or deleted), so the tables stay small. The
lifecycle that works on them is `apps.core.services.exports`.

The subclass names its own table (the default `<app>_<model>`), indexes and the "one building
export per user" constraint (a partial unique constraint: what lets `create_export` take no lock
on the user's account while it writes the possibly huge plan). Every column here has a
`db_default`, like every new column in this repo (the container still serving during a migration
doesn't know it).
"""

import uuid
from datetime import datetime
from typing import cast

from django.db import models


class ExportStatus(models.TextChoices):
    """`BUILDING` -> `READY` | `FAILED`; `READY` -> `EXPIRED` (after the retention days) or
    `DELETED` (the user deleted it). A failed one is refunded in full."""

    BUILDING = 'building', 'Preparing'
    READY = 'ready', 'Ready'
    FAILED = 'failed', 'Failed'
    EXPIRED = 'expired', 'Expired'
    DELETED = 'deleted', 'Deleted'


class BaseExport(models.Model):
    """One "download my files" request. Charged in full when it is created (`charged_credits`,
    at `price_per_file` each); a failed build refunds all of it, a file that had vanished by the
    time it was zipped refunds its own share (`refunded_credits`). The status change that does a
    refund is the compare-and-swap that makes it happen once."""

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=ExportStatus.choices,
            default=ExportStatus.BUILDING,
            db_default=ExportStatus.BUILDING,
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
            default=0,
            db_default=0,
            help_text='Times a worker took the part being built without it finishing (reset '
            'by every part that is built); the export fails at `MAX_ATTEMPTS`.',
        ),
    )
    progress_at = cast(
        datetime,
        models.DateTimeField(
            auto_now_add=True,
            help_text="When the build last made progress; the app's retry job re-queues one "
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
        abstract = True

    def __str__(self) -> str:
        return f'Export {self.pk} ({self.status})'

    @property
    def is_building(self) -> bool:
        return self.status == ExportStatus.BUILDING

    @property
    def is_ready(self) -> bool:
        return self.status == ExportStatus.READY


class BaseExportPart(models.Model):
    """One zip file of an export. `built_at` is null until it is written. The subclass adds the
    `export` foreign key (`related_name='parts'`)."""

    export_id: uuid.UUID

    id = models.BigAutoField(primary_key=True)
    number = cast(int, models.PositiveIntegerField())
    key = cast(str, models.CharField(max_length=512))
    size_bytes = cast(int, models.PositiveBigIntegerField(default=0, db_default=0))
    file_count = cast(int, models.PositiveIntegerField(default=0, db_default=0))
    built_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))

    class Meta:
        abstract = True
        ordering = ['number']

    def __str__(self) -> str:
        return f'Export {self.export_id}, part {self.number}'


class BaseExportItem(models.Model):
    """One file in an export: where it is, where it goes in the zips and which zip. What the
    index file (Receipts' CSV, To-do's workbook) needs besides is a copy the subclass adds, taken
    when the export was planned, so a later edit or delete of the record changes nothing; the id
    of the record is a plain UUID, never a foreign key: one deleted for good meanwhile must not
    break the export (its file is then simply `skipped`). The subclass adds the `export` foreign
    key (`related_name='items'`)."""

    export_id: uuid.UUID

    id = models.BigAutoField(primary_key=True)
    part_number = cast(int, models.PositiveIntegerField())
    file_key = cast(str, models.CharField(max_length=512))
    path = cast(str, models.CharField(max_length=512, help_text='The name inside the zip.'))
    size_bytes = cast(int, models.PositiveBigIntegerField(default=0, db_default=0))
    skipped = cast(bool, models.BooleanField(default=False, db_default=False))

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.path
