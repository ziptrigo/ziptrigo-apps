"""Exports (issue #326): "Download files" packs the files attached to the user's tasks and to the
comments on them into independent zip files (about `TodoSettings.export_part_bytes` each, no
compression) with one workbook of the tasks and their comments (`tasks.xlsx`, in part 1), kept in S3
for `export_retention_days` and then deleted. "Export XLSX" is that workbook on its own, free.

The lifecycle from the moment the rows are written (the build, the refunds, the resilience, the
download and the ending) is shared with Receipts' export and lives in
`apps.core.services.exports`, described there; this module is To-do's side of it: `KIND`, the
public functions that call the engine with it, and what is To-do's own.

**What is exported.** The user's live tasks (never the Trash) **created** in a date range
(`export_range.ExportRange`: the account page's "Task created" select), every state and every
subtask, and the uploaded files on them: the task's own and the comments' that are not deleted.
Never a draft comment upload (a file uploaded for a comment that was never saved), a file whose
upload didn't complete, or anything of a comment in its deleted state. One file = one price.

**Flow.** `prepare_export` reads the files and plans them (`export_plan.plan_export`) with no lock;
`quote_export` is its `Quote` (no writes). `check_export` is `create_export`'s refusals, told with
no lock and no write; `create_export` re-checks in one transaction only what can have changed
(frozen, the number of files, the settings), raises `ExportCostChangedError` (nothing charged) when
the cost is not what the user confirmed, writes the export, its parts and its items and only then
spends the credits and queues the first part. **It takes no lock on the To-do account**: "one
export at a time" is the partial unique constraint `todo_export_one_building_per_user` (an
`IntegrityError` becomes `ExportInProgressError`). **Nothing to export is refused**: with no file
in the range there is no zip to make (the task list is the free XLSX).

**The workbook** is written when part 1 is built (last), from the database as of then, for the
instants the export keeps (`filters['start']`/`['end']`), **only tasks created, and comments
written, up to the moment it was asked for**; its files column is written from the items (the
paths in the zips). The files in the zips were fixed when the export was asked for, so the tasks
and comments the items refer to are written whatever has happened to them since (a task moved to
the Trash or a comment deleted in between still has its row), and the rare file whose task or
comment row no longer exists at all goes into a "Files not listed above" sheet: no file in the
zips is unlisted. The quick XLSX (`build_xlsx`) lists file names instead of paths.
"""

import logging
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import IO
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.tasks import task

from apps.accounts.models import User
from apps.accounts.services.timezones import user_tz
from apps.billing.models import CreditTransactionType
from apps.billing.services import (
    InsufficientCreditsError,
    add_credits,
    get_balance,
    spend_credits,
)
from apps.core.services import exports as engine
from apps.core.services.export_errors import ExportCostChangedError, ExportInProgressError
from apps.core.services.exports import (  # noqa: F401  (re-exported for the callers and tests)
    ENDED_STATUSES,
    LEASE,
    MAX_ATTEMPTS,
    RETRY_BACKOFF,
    STALL_AFTER,
    WRITER_PART_BYTES,
    ExportKind,
    Quote,
    build_order,
    first_part_to_build,
)
from apps.core.services.queue import enqueue_after_commit

from ..models import (
    Attachment,
    ExportStatus,
    TodoExport,
    TodoExportItem,
    TodoExportPart,
    TodoSettings,
)
from .accounts import is_frozen
from .emails import send_export_ready_email
from .errors import AccountFrozenError
from .export_plan import DOWNLOAD_PREFIX, ExportRow, Plan, plan_export
from .export_range import ExportRange
from .export_workbook import (
    WORKBOOK_NAME,
    FileListing,
    WorkbookStats,
    task_scope,
    write_workbook,
)
from .storage import attachment_key, export_key, export_prefix, get_storage

logger = logging.getLogger(__name__)

#: Nothing to download: the message the dialog, the page and the API show.
NOTHING_TO_EXPORT = 'No files in this range; use Export XLSX for the task list.'


# -- Reading and planning -----------------------------------------------------------------------


def _file_scope() -> Q:
    """The files of the tasks in the range that are exported: uploaded ones, the task's own and
    the saved comments' that are not deleted (a draft comment upload has `for_comment` and no
    comment, and a deleted comment's files are left out with it)."""
    return Q(uploaded=True) & (
        Q(comment__isnull=True, for_comment=False)
        | Q(comment__isnull=False, comment__deleted_at__isnull=True)
    )


def _attachments(user: User, start: datetime | None, end: datetime | None):
    return Attachment.objects.filter(
        _file_scope(), **task_scope(user, start, end, prefix='task__')
    ).order_by()


def select_rows(user: User, start: datetime | None, end: datetime | None) -> list[ExportRow]:
    """The files of the user's tasks created in `[start, end)` (see the module docstring)."""
    queryset = _attachments(user, start, end).values_list(
        'id',
        'task_id',
        'comment_id',
        'task__number',
        'task__title',
        'task__project__name',
        'size_bytes',
        'filename',
        'created_at',
    )
    return [
        ExportRow(
            attachment_id=attachment_id,
            task_id=task_id,
            comment_id=comment_id,
            task_number=number,
            task_title=title,
            project=project or '',
            file_key=attachment_key(user.pk, attachment_id),
            size_bytes=size_bytes,
            file_name=filename,
            created_at=created_at,
        )
        for (
            attachment_id,
            task_id,
            comment_id,
            number,
            title,
            project,
            size_bytes,
            filename,
            created_at,
        ) in queryset.iterator(chunk_size=1000)
    ]


@dataclass(frozen=True, slots=True)
class Prepared:
    """What reading and planning the files came to: the plan, the settings it was made with and
    the quote. Made once per request, outside any lock, and handed from `check_export` to
    `create_export` so a confirm scans the files once."""

    export_range: ExportRange
    plan: Plan
    config: TodoSettings
    quote: Quote

    @property
    def cost(self) -> int:
        return self.quote.cost


def prepare_export(user: User, export_range: ExportRange | None = None) -> Prepared:
    """Read what the range lists and plan it. No writes, no locks."""
    export_range = export_range or ExportRange()
    config = TodoSettings.load()
    start, end = export_range.bounds(user)
    plan = plan_export(select_rows(user, start, end), config.export_part_bytes)
    quote = Quote(
        file_count=len(plan.items),
        total_bytes=plan.total_bytes,
        part_count=plan.part_count,
        price_per_file=config.export_price_per_file,
        cost=len(plan.items) * config.export_price_per_file,
        balance=get_balance(user),
        frozen=is_frozen(user.pk),
        retention_days=config.export_retention_days,
        part_bytes=config.export_part_bytes,
    )
    return Prepared(export_range=export_range, plan=plan, config=config, quote=quote)


def quote_export(user: User, export_range: ExportRange | None = None) -> Quote:
    """The number of files, their size, the number of zip files and the cost. No writes."""
    return prepare_export(user, export_range).quote


# -- Creating -----------------------------------------------------------------------------------


def _refuse_early(user: User) -> None:
    """The refusals that need no reading of the files."""
    if is_frozen(user.pk):
        raise AccountFrozenError()
    if TodoExport.objects.filter(user=user, status=ExportStatus.BUILDING).exists():
        raise ExportInProgressError()


def _refuse_for(prepared: Prepared, expected_cost: int | None) -> None:
    if not prepared.plan.items:
        raise ValidationError(NOTHING_TO_EXPORT)
    if expected_cost is not None and expected_cost != prepared.cost:
        raise ExportCostChangedError(prepared.cost)


def check_export(
    user: User,
    export_range: ExportRange | None,
    *,
    expected_cost: int | None = None,
    prepared: Prepared | None = None,
) -> Prepared:
    """`create_export`'s refusals, told without locks or writes, so a view can refuse before it
    spends a rate-limit hit. The same exceptions; `create_export` decides again before it writes.
    Returns what it read, to pass on as `prepared` (a caller that already has one passes it in and
    nothing is read again)."""
    _refuse_early(user)
    prepared = prepared or prepare_export(user, export_range)
    _refuse_for(prepared, expected_cost)
    if prepared.cost and get_balance(user) < prepared.cost:
        raise InsufficientCreditsError()
    return prepared


def _count_files(user: User, export_range: ExportRange) -> int:
    start, end = export_range.bounds(user)
    return _attachments(user, start, end).count()


def _recheck(
    user: User, export_range: ExportRange, prepared: Prepared, expected_cost: int | None
) -> Prepared:
    """Inside the transaction, right before anything is written: only what can have changed since
    the files were planned -- the frozen state, the number of files and the settings. If any
    differs they are planned again (rare), so what is charged is always what is written."""
    if is_frozen(user.pk):
        raise AccountFrozenError()
    config = TodoSettings.load()
    if (
        _count_files(user, export_range) != len(prepared.plan.items)
        or config.export_part_bytes != prepared.config.export_part_bytes
        or config.export_price_per_file != prepared.config.export_price_per_file
    ):
        prepared = prepare_export(user, export_range)
    _refuse_for(prepared, expected_cost)
    return prepared


def create_export(
    user: User,
    export_range: ExportRange | None,
    *,
    expected_cost: int | None = None,
    prepared: Prepared | None = None,
) -> TodoExport:
    """Charge for and start an export of the files of the tasks created in `export_range`.

    Raises `AccountFrozenError` (storage unpaid), `ExportInProgressError` (one at a time),
    `ValidationError` (nothing to export), `ExportCostChangedError` (the cost is not
    `expected_cost`; `None` accepts whatever it is, for a client with no one to ask) and
    `InsufficientCreditsError`. A refusal changes and charges nothing. `prepared` is what
    `check_export` returned; it is re-checked, not trusted.

    The export, its parts and its items are written in one transaction with the charge, which is
    the last write (it locks the billing row, so it is held for as short a time as possible). No
    lock on the To-do account is taken.
    """
    export_range = export_range or ExportRange()
    _refuse_early(user)
    prepared = prepared or prepare_export(user, export_range)
    _refuse_for(prepared, expected_cost)
    try:
        with transaction.atomic():
            prepared = _recheck(user, export_range, prepared, expected_cost)
            export = _write_export(user, export_range, prepared)
            if prepared.cost:
                spend_credits(
                    user,
                    prepared.cost,
                    description=f'To-do export: {len(prepared.plan.items)} files',
                    source='todo',
                )
            enqueue_after_commit(build_part, str(export.pk), first_part_to_build(export.part_count))
    except IntegrityError:
        # Two requests passed the early check together: the constraint lets one through.
        if TodoExport.objects.filter(user=user, status=ExportStatus.BUILDING).exists():
            raise ExportInProgressError() from None
        raise
    return export


def _write_export(user: User, export_range: ExportRange, prepared: Prepared) -> TodoExport:
    plan = prepared.plan
    start, end = export_range.bounds(user)
    export = TodoExport.objects.create(
        user=user,
        filters={
            'summary': export_range.describe(user),
            'params': export_range.params,
            'start': start.isoformat() if start else None,
            'end': end.isoformat() if end else None,
        },
        file_count=len(plan.items),
        total_bytes=plan.total_bytes,
        part_count=plan.part_count,
        price_per_file=prepared.config.export_price_per_file,
        charged_credits=prepared.cost,
    )
    per_part = Counter(item.part_number for item in plan.items)
    TodoExportPart.objects.bulk_create(
        TodoExportPart(
            export=export,
            number=number,
            key=export_key(user.pk, export.pk, number),
            file_count=per_part[number],
        )
        for number in range(1, plan.part_count + 1)
    )
    TodoExportItem.objects.bulk_create(
        (
            TodoExportItem(
                export=export,
                part_number=item.part_number,
                attachment_id=item.row.attachment_id,
                task_id=item.row.task_id,
                comment_id=item.row.comment_id,
                file_key=item.row.file_key,
                path=item.path,
                size_bytes=item.row.size_bytes,
            )
            for item in plan.items
        ),
        batch_size=1000,
    )
    return export


# -- The workbook ---------------------------------------------------------------------------------


def _bounds_of(export: TodoExport) -> tuple[datetime | None, datetime | None]:
    filters = export.filters or {}
    return tuple(  # ty: ignore[invalid-return-type]
        datetime.fromisoformat(value) if value else None
        for value in (filters.get('start'), filters.get('end'))
    )


def _part_suffix(part_number: int, part_count: int) -> str:
    return f' (part {part_number})' if part_count > 1 else ''


def listing_for_export(export: TodoExport) -> FileListing:
    """What the workbook's files cells say: each file that went into a zip, by its path there."""
    listing = FileListing()
    for task_id, comment_id, path, part_number in (
        export.items.filter(skipped=False)
        .order_by('part_number', 'path')
        .values_list('task_id', 'comment_id', 'path', 'part_number')
        .iterator(chunk_size=1000)
    ):
        listing.add(task_id, comment_id, f'{path}{_part_suffix(part_number, export.part_count)}')
    return listing


def listing_by_name(user: User, start: datetime | None, end: datetime | None) -> FileListing:
    """What the quick XLSX's files cells say: the file names (there are no zip paths)."""
    listing = FileListing()
    for task_id, comment_id, filename in (
        _attachments(user, start, end)
        .order_by('created_at', 'id')
        .values_list('task_id', 'comment_id', 'filename')
        .iterator(chunk_size=1000)
    ):
        listing.add(task_id, comment_id, filename)
    return listing


def build_xlsx(user: User, export_range: ExportRange | None, target: IO[bytes]) -> WorkbookStats:
    """The quick "Export XLSX": the workbook of the tasks created in the range, with file names
    in the files columns, written into `target`. Free; nothing is stored."""
    start, end = (export_range or ExportRange()).bounds(user)
    return write_workbook(
        target,
        user,
        start=start,
        end=end,
        files=listing_by_name(user, start, end),
    )


def _write_workbook(export: TodoExport, zf: zipfile.ZipFile) -> None:
    """`tasks.xlsx` into part 1's zip: built into a temporary file (a workbook is a zip itself,
    which needs to seek), then copied in a chunk at a time."""
    start, end = _bounds_of(export)
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as buffer:
        write_workbook(
            buffer,
            export.user,
            start=start,
            end=end,
            files=listing_for_export(export),
            as_of=export.created_at,
        )
        buffer.seek(0)
        engine.write_zip_entry(zf, export, WORKBOOK_NAME, iter(lambda: buffer.read(1 << 20), b''))


# -- The kind -------------------------------------------------------------------------------------


def _refund(user: User, credits: int, description: str) -> None:
    add_credits(
        user,
        credits,
        tx_type=CreditTransactionType.REFUND,
        description=description,
        source='todo',
    )


def _can_notify(user: User) -> bool:
    return bool(user.is_active and user.status == User.STATUS_ACTIVE)


def _is_frozen(user: User) -> bool:
    return is_frozen(user.pk)


@task(queue_name='default')
def build_part(export_id: str, number: int) -> None:
    """Build one zip file of an export, then queue the next, or finish the export. Never raises:
    a failure is retried (`resume_stalled`) until the attempts run out, then fails the export."""
    engine.build_part(KIND, export_id, number)


KIND = ExportKind(
    source='todo',
    label='To-do export',
    ready_title='Your to-do export is ready',
    failed_title='Your to-do export could not be prepared',
    file_noun='file',
    download_prefix=DOWNLOAD_PREFIX,
    detail_url_name='todo:export-detail',
    export_model=TodoExport,
    part_model=TodoExportPart,
    item_model=TodoExportItem,
    build_task=build_part,
    send_ready_email=send_export_ready_email,
    load_config=TodoSettings.load,
    get_storage=get_storage,
    export_prefix=export_prefix,
    is_frozen=_is_frozen,
    frozen_error=AccountFrozenError,
    refund=_refund,
    can_notify=_can_notify,
    write_index=_write_workbook,
    timezone_of=user_tz,
)


def notice_key(export_id: object) -> str:
    return engine.notice_key(KIND, export_id)


def export_url_path(export_id: object) -> str:
    return engine.export_url_path(KIND, export_id)


# -- Building, resuming, downloading and ending (the shared lifecycle, `KIND`'s) -----------------


def next_part_to_build(export: TodoExport) -> int | None:
    return engine.next_part_to_build(export)


def resume_stalled(now: datetime | None = None) -> int:
    """Queue the next part of every build nobody is working on (`todo.retry_exports`)."""
    return engine.resume_stalled(KIND, now)


def finish_export(export_id: UUID) -> bool:
    """Every part is built: the export is ready, what had vanished is refunded, the user told."""
    return engine.finish_export(KIND, export_id)


def fail_export(export_id: UUID, reason: str = '') -> bool:
    """The build failed for good: refund everything charged, tell the user, delete the files."""
    return engine.fail_export(KIND, export_id, reason)


def part_name(export: TodoExport, number: int) -> str:
    """The name a part is downloaded as."""
    return engine.part_name(KIND, export, number)


def part_download(export: TodoExport, number: int) -> tuple[str, TodoExportPart]:
    """A short-lived "Save As" URL for one zip file of a ready export, with that file's row."""
    return engine.part_download(KIND, export, number)


def part_download_url(export: TodoExport, number: int) -> str:
    """`part_download`'s URL alone."""
    return engine.part_download_url(KIND, export, number)


def discard(export: TodoExport) -> bool:
    """Delete an ended export's objects, then its parts and items rows."""
    return engine.discard(KIND, export)


def delete_export(export: TodoExport) -> bool:
    """The user deletes an export: its files go now. No refund. Idempotent."""
    return engine.delete_export(KIND, export)


def expire_exports(now: datetime | None = None) -> int:
    """Daily: exports past `expires_at` are marked expired and their files deleted."""
    return engine.expire_exports(KIND, now)
