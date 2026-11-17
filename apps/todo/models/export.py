"""Exports (issue #326): a user's task files -- the files attached to their tasks and to the
comments on them -- packed into independent zip files kept in S3 for a while, next to one
spreadsheet of the tasks and their comments. `TodoExport` is one request, its `TodoExportPart`s
are the zip files and its `TodoExportItem`s are the files that go into them (the plan). Items and
parts are deleted when the export ends (expired, failed or deleted), so the tables stay small.

The columns every product's export has are the abstract bases in `apps.core.models.exports`
(shared with Receipts, issue #315); what is To-do's own is here: the owner, what was selected, and
the ids an item needs to be listed against its task and comment in the workbook.

Every new column has a `db_default` (the container still serving during the migration doesn't know
them).
"""

import uuid
from typing import TYPE_CHECKING, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User
from apps.core.models.exports import BaseExport, BaseExportItem, BaseExportPart, ExportStatus

if TYPE_CHECKING:
    from django.db.models import Manager

__all__ = ['ExportStatus', 'TodoExport', 'TodoExportItem', 'TodoExportPart']


class TodoExport(BaseExport):
    """One "download my task files" request (the shared lifecycle columns are `BaseExport`'s)."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    # The reverse accessors (`related_name`), declared for the type checker.
    parts: ClassVar['Manager']
    items: ClassVar['Manager']

    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='todo_exports'
        ),
    )
    user_id: uuid.UUID
    filters = cast(
        dict,
        models.JSONField(
            default=dict,
            db_default={},
            blank=True,
            help_text='What was selected when the export was asked for: `summary` (lines for '
            'display), `params` (the account page query, `date_range` / `date_from` / `date_to`) '
            'and `start` / `end` (the instants: tasks created from `start`, before `end`; either '
            'may be null).',
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
                name='todo_export_one_building_per_user',
            )
        ]
        ordering = ['-created_at']


class TodoExportPart(BaseExportPart):
    """One zip file of an export. `built_at` is null until it is written."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    export = cast(
        'TodoExport',
        models.ForeignKey(TodoExport, on_delete=models.CASCADE, related_name='parts'),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['export', 'number'], name='todo_export_part_unique')
        ]
        ordering = ['number']


class TodoExportItem(BaseExportItem):
    """One attached file in an export. `attachment_id`, `task_id` and `comment_id` are plain UUIDs,
    not foreign keys: a task or an attachment deleted for good meanwhile must not break the export
    (its file is then simply `skipped`). `comment_id` is null for a file attached to the task
    itself; the workbook lists the file against its task, or its comment, by these ids."""

    objects: ClassVar['Manager']

    export = cast(
        'TodoExport',
        models.ForeignKey(TodoExport, on_delete=models.CASCADE, related_name='items'),
    )
    attachment_id = cast(uuid.UUID, models.UUIDField())
    task_id = cast(uuid.UUID, models.UUIDField())
    comment_id = cast('uuid.UUID | None', models.UUIDField(null=True, blank=True))

    class Meta:
        indexes = [models.Index(fields=['export', 'part_number'])]
        ordering = ['part_number', 'path']
