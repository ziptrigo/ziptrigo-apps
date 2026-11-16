"""Exports (issue #315): "Download files" packs the user's receipt files into independent zip files
(about `ReceiptsSettings.export_part_bytes` each, no compression) with one CSV of their data, kept
in S3 for `export_retention_days` and then deleted.

**Flow.** `quote_export` says what an export would cost (no writes). `create_export` runs in one
transaction under the account row lock: it reads what the filters list, plans it
(`export_plan.plan_export`), recomputes the cost and raises `ExportCostChangedError` (nothing
charged) when it is not the `expected_cost` the user confirmed, spends the credits, writes the
export, its parts and its items, and queues the first part. **One part is built per task**
(`build_part`, the `receipts` queue), each queueing the next, so a long export never holds a worker
in one piece and a restart resumes at the unfinished part. Part 1 is built **last**: it carries
`receipts.csv`, which lists every part's files and must say what really went in. After the last
part the export is `ready`: `expires_at` is set, the files that had vanished are refunded, and the
user gets a notification and an email.

**Money.** Charged in full at creation (`price_per_file` is stored on the export). A file whose S3
object is gone when its part is built is left out (`ReceiptExportItem.skipped`) and refunded at
the end. A build that fails for good refunds everything. Both refunds happen in the very
transaction of the status change that is their compare-and-swap (`building` -> `ready` /
`failed`), so each happens once. Deleting an export, or letting it expire, refunds nothing.

**Resilience.** A part is claimed with a lease (`lease_until`); a part that raises counts an
attempt on the export (reset by the next success) and waits `RETRY_BACKOFF`; `MAX_ATTEMPTS`
consecutive failures fail the export. `receipts.retry_due` calls `resume_stalled`, which queues the
next unbuilt part of a build that is neither leased nor progressing (a dead worker, a task that was
never queued). Every step is idempotent: a part is written under a deterministic key and marked
built with one conditional update.

**Ending.** `expire` (the daily `receipts.purge_exports` job), `delete_export` (the user) and a
failure all end an export the same way: status first (the compare-and-swap), then the objects
(`delete_prefix`), then the parts and items rows. The job also finishes any ended export whose
objects or rows are still there (a delete that failed), so a failure to delete never leaks.
Exports are not part of the account's `stored_bytes`: their price covers them.
"""

import logging
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from botocore.exceptions import ClientError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.tasks import task
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import CreditTransactionType
from apps.billing.services import (
    InsufficientCreditsError,
    add_credits,
    get_balance,
    spend_credits,
)
from apps.core.datetime_format import PUBLIC, format_date
from apps.core.services import notifications
from apps.core.services.queue import enqueue_after_commit
from apps.core.services.zip_stream import S3MultipartWriter, copy_stream

from ..models import (
    ExportLayout,
    ExportStatus,
    ReceiptCategory,
    ReceiptExport,
    ReceiptExportItem,
    ReceiptExportPart,
    ReceiptsAccount,
    ReceiptsSettings,
    ReceiptStatus,
    Vendor,
)
from .accounts import get_account, is_frozen
from .emails import send_export_ready_email
from .errors import AccountFrozenError, ExportCostChangedError, ExportInProgressError
from .export_plan import (
    CSV_NAME,
    CsvEntry,
    ExportRow,
    Plan,
    iter_export_csv,
    part_file_name,
    plan_export,
)
from .files import default_name_for
from .filters import UNCATEGORIZED, ReceiptFilters, _statuses, filter_receipts
from .storage import export_key, export_prefix, get_storage

logger = logging.getLogger(__name__)

#: Consecutive failed tries of a part before the export fails (and is refunded).
MAX_ATTEMPTS = 3

#: How long a worker may hold a part. A part is at most about `export_part_bytes` read from S3 and
#: written back, a few minutes; this is the time after which a dead worker's part is taken over.
LEASE = timedelta(minutes=15)

#: How long a part that raised waits before it is tried again.
RETRY_BACKOFF = timedelta(minutes=2)

#: `resume_stalled` re-queues a build with no lease that has not progressed for this long.
STALL_AFTER = timedelta(minutes=5)

#: What the zip writer buffers before it uploads an S3 part (S3 needs at least 5 MB).
WRITER_PART_BYTES = 16 * 1024 * 1024

#: Notification keys (`receipts_export:<id>`), so one export has one notice, the latest.
NOTICE_KEY_PREFIX = 'receipts_export:'

ENDED_STATUSES = (ExportStatus.FAILED, ExportStatus.EXPIRED, ExportStatus.DELETED)


def notice_key(export_id: object) -> str:
    return f'{NOTICE_KEY_PREFIX}{export_id}'


def export_url_path(export_id: object) -> str:
    return reverse('receipts:export-detail', args=[export_id])


# -- Reading and planning -----------------------------------------------------------------------


def select_rows(user: User, filters: ReceiptFilters | None) -> list[ExportRow]:
    """The files `filters` list: every live receipt with a stored file (the Trash and unfinished
    uploads never)."""
    queryset = (
        filter_receipts(user, filters)
        .exclude(file_key='')
        .order_by()
        .values_list(
            'id',
            'file_key',
            'size_bytes',
            'original_filename',
            'content_type',
            'receipt_date',
            'vendor',
            'document_type',
            'amount',
            'currency',
            'created_at',
        )
    )
    return [
        ExportRow(
            receipt_id=receipt_id,
            file_key=file_key,
            size_bytes=size_bytes,
            file_name=original_filename or default_name_for(content_type),
            receipt_date=receipt_date,
            vendor=vendor,
            document_type=document_type,
            amount=amount,
            currency=currency,
            created_at=created_at,
        )
        for (
            receipt_id,
            file_key,
            size_bytes,
            original_filename,
            content_type,
            receipt_date,
            vendor,
            document_type,
            amount,
            currency,
            created_at,
        ) in queryset.iterator(chunk_size=1000)
    ]


def clean_layout(layout: str) -> str:
    if layout not in ExportLayout.values:
        raise ValidationError({'layout': 'Choose how to organize the files.'})
    return layout


@dataclass(frozen=True, slots=True)
class Quote:
    """What an export of the current filters would be."""

    file_count: int
    total_bytes: int
    part_count: int
    price_per_file: int
    cost: int
    balance: int
    frozen: bool
    retention_days: int
    part_bytes: int

    @property
    def affordable(self) -> bool:
        return self.balance >= self.cost


def quote_export(
    user: User, filters: ReceiptFilters | None = None, layout: str = ExportLayout.YEAR_VENDOR
) -> Quote:
    """The number of files, their size, the number of zip files and the cost. No writes."""
    layout = clean_layout(layout)
    config = ReceiptsSettings.load()
    plan = plan_export(select_rows(user, filters), layout, config.export_part_bytes)
    return Quote(
        file_count=len(plan.items),
        total_bytes=plan.total_bytes,
        part_count=plan.part_count,
        price_per_file=config.export_price_per_file,
        cost=len(plan.items) * config.export_price_per_file,
        balance=get_balance(user),
        frozen=is_frozen(user),
        retention_days=config.export_retention_days,
        part_bytes=config.export_part_bytes,
    )


def filters_params(filters: ReceiptFilters | None) -> dict:
    """`filters` as plain JSON (what the export keeps to say what it was made from)."""
    filters = filters or ReceiptFilters()
    params: dict = {}
    if filters.status:
        params['status'] = ','.join(_statuses(filters.status))
    if filters.category not in (None, ''):
        params['category'] = str(filters.category)
    if filters.vendor is not None:
        params['vendor'] = int(filters.vendor)
    if filters.q.strip():
        params['q'] = filters.q.strip()
    if filters.date_from:
        params['date_from'] = filters.date_from.isoformat()
    if filters.date_to:
        params['date_to'] = filters.date_to.isoformat()
    if filters.duplicates:
        params['duplicates'] = True
    return params


def describe_filters(user: User, filters: ReceiptFilters | None) -> list[str]:
    """What was selected, as short lines for the export's page ("Vendor: Acme"); empty when the
    export is of everything."""
    filters = filters or ReceiptFilters()
    lines: list[str] = []
    statuses = _statuses(filters.status)
    if statuses:
        in_progress = {s.value for s in (ReceiptStatus.RECEIVED, ReceiptStatus.PROCESSING)} | {
            ReceiptStatus.WAITING_FOR_CREDITS.value
        }
        label = (
            'Processing'
            if set(statuses) == in_progress
            else ', '.join(ReceiptStatus(s).label for s in statuses)
        )
        lines.append(f'Status: {label}')
    if filters.category not in (None, ''):
        if filters.category == UNCATEGORIZED:
            lines.append('Category: none')
        else:
            category = ReceiptCategory.objects.filter(
                user=user, pk=str(filters.category).strip() or 0
            ).first()
            lines.append(f'Category: {category.name if category else "unknown"}')
    if filters.vendor is not None:
        vendor = Vendor.objects.filter(user=user, pk=filters.vendor).first()
        lines.append(f'Vendor: {vendor.name if vendor else "unknown"}')
    if filters.q.strip():
        lines.append(f'Search: "{filters.q.strip()}"')
    if filters.date_from and filters.date_to:
        first = format_date(filters.date_from, 'full', PUBLIC)
        last = format_date(filters.date_to, 'full', PUBLIC)
        lines.append(f'Receipt date: {first} to {last}')
    elif filters.date_from:
        lines.append(f'Receipt date: from {format_date(filters.date_from, "full", PUBLIC)}')
    elif filters.date_to:
        lines.append(f'Receipt date: until {format_date(filters.date_to, "full", PUBLIC)}')
    if filters.duplicates:
        lines.append('Possible duplicates only')
    return lines


# -- Creating -----------------------------------------------------------------------------------


def _judge(
    user: User, filters: ReceiptFilters | None, layout: str, expected_cost: int | None
) -> tuple[Plan, ReceiptsSettings, int]:
    """Every refusal an export can meet, with no writes: the plan, the settings and the cost."""
    layout = clean_layout(layout)
    if is_frozen(user):
        raise AccountFrozenError()
    if ReceiptExport.objects.filter(user=user, status=ExportStatus.BUILDING).exists():
        raise ExportInProgressError()
    config = ReceiptsSettings.load()
    plan = plan_export(select_rows(user, filters), layout, config.export_part_bytes)
    if not plan.items:
        raise ValidationError('There are no files to download with these filters.')
    cost = len(plan.items) * config.export_price_per_file
    if expected_cost is not None and expected_cost != cost:
        raise ExportCostChangedError(cost)
    return plan, config, cost


def check_export(
    user: User,
    filters: ReceiptFilters | None,
    layout: str,
    *,
    expected_cost: int | None = None,
) -> None:
    """`create_export`'s refusals, told without locks or writes, so a view can refuse before it
    spends a rate-limit hit. The same exceptions; `create_export` decides again under its lock."""
    _, _, cost = _judge(user, filters, layout, expected_cost)
    if cost and get_balance(user) < cost:
        raise InsufficientCreditsError()


def create_export(
    user: User,
    filters: ReceiptFilters | None,
    layout: str,
    *,
    expected_cost: int | None = None,
) -> ReceiptExport:
    """Charge for and start an export of what `filters` list.

    Raises `AccountFrozenError` (storage unpaid), `ExportInProgressError` (one at a time),
    `ValidationError` (an unknown layout, nothing to export), `ExportCostChangedError` (the cost
    is not `expected_cost`; `None` accepts whatever it is, for a client with no one to ask) and
    `InsufficientCreditsError`. A refusal changes and charges nothing.
    """
    layout = clean_layout(layout)
    account = get_account(user)
    with transaction.atomic():
        ReceiptsAccount.objects.select_for_update().get(pk=account.pk)
        plan, config, cost = _judge(user, filters, layout, expected_cost)
        if cost:
            spend_credits(
                user,
                cost,
                description=f'Receipts export: {len(plan.items)} files',
                source='receipts',
            )
        export = _write_export(user, filters, layout, plan, config.export_price_per_file, cost)
        enqueue_after_commit(build_part, str(export.pk), first_part_to_build(export.part_count))
    return export


def _write_export(
    user: User,
    filters: ReceiptFilters | None,
    layout: str,
    plan: Plan,
    price: int,
    cost: int,
) -> ReceiptExport:
    export = ReceiptExport.objects.create(
        user=user,
        layout=layout,
        filters={
            'summary': describe_filters(user, filters),
            'params': filters_params(filters),
        },
        file_count=len(plan.items),
        total_bytes=plan.total_bytes,
        part_count=plan.part_count,
        price_per_file=price,
        charged_credits=cost,
    )
    ReceiptExportPart.objects.bulk_create(
        ReceiptExportPart(
            export=export,
            number=number,
            key=export_key(user.pk, export.pk, number),
            file_count=sum(1 for item in plan.items if item.part_number == number),
        )
        for number in range(1, plan.part_count + 1)
    )
    ReceiptExportItem.objects.bulk_create(
        (
            ReceiptExportItem(
                export=export,
                part_number=item.part_number,
                receipt_id=item.row.receipt_id,
                file_key=item.row.file_key,
                path=item.path,
                size_bytes=item.row.size_bytes,
                receipt_date=item.row.receipt_date,
                vendor=item.row.vendor,
                document_type=item.row.document_type,
                amount=item.row.amount,
                currency=item.row.currency,
            )
            for item in plan.items
        ),
        batch_size=1000,
    )
    return export


# -- Building -----------------------------------------------------------------------------------


def build_order(part_count: int) -> list[int]:
    """The order parts are built in: 2, 3, ... then 1 last (it holds the CSV of everything)."""
    return [*range(2, part_count + 1), 1] if part_count else []


def first_part_to_build(part_count: int) -> int:
    return build_order(part_count)[0]


def next_part_to_build(export: ReceiptExport) -> int | None:
    built = set(export.parts.filter(built_at__isnull=False).values_list('number', flat=True))
    for number in build_order(export.part_count):
        if number not in built:
            return number
    return None


def _is_missing(exc: ClientError) -> bool:
    return exc.response.get('Error', {}).get('Code', '') in ('NoSuchKey', '404', 'NotFound')


def _stamp(export: ReceiptExport) -> tuple[int, int, int, int, int, int]:
    return export.created_at.timetuple()[:6]


def _csv_entries(export: ReceiptExport) -> Iterator[CsvEntry]:
    for item in (
        export.items.filter(skipped=False).order_by('part_number', 'path').iterator(chunk_size=1000)
    ):
        yield CsvEntry(
            receipt_date=item.receipt_date,
            vendor=item.vendor,
            document_type=item.document_type,
            amount=item.amount,
            currency=item.currency,
            path=item.path,
            part_number=item.part_number,
        )


def _write_part(export: ReceiptExport, part: ReceiptExportPart) -> tuple[int, int]:
    """Write one part's zip to S3. Returns its size and how many files went in."""
    storage = get_storage()
    items = list(export.items.filter(part_number=part.number, skipped=False).order_by('path'))
    writer = S3MultipartWriter(storage, part.key, part_size=WRITER_PART_BYTES)
    written = 0
    try:
        # `S3MultipartWriter` deliberately has no `tell`/`seekable`, which no `ZipFile` overload
        # names: see its docstring.
        with zipfile.ZipFile(  # ty: ignore[no-matching-overload]
            writer, mode='w', compression=zipfile.ZIP_STORED, allowZip64=True
        ) as zf:
            gone: list[int] = []
            for item in items:
                try:
                    stream = storage.get_object_stream(item.file_key)
                except ClientError as exc:
                    if not _is_missing(exc):
                        raise
                    gone.append(item.pk)
                    continue
                info = zipfile.ZipInfo(item.path, date_time=_stamp(export))
                info.compress_type = zipfile.ZIP_STORED
                with zf.open(info, mode='w', force_zip64=True) as dest:
                    copy_stream(stream, dest)
                written += 1
            if gone:
                ReceiptExportItem.objects.filter(pk__in=gone).update(skipped=True)
            if part.number == 1:
                # Last, and from the database: what was skipped anywhere is already marked.
                info = zipfile.ZipInfo(CSV_NAME, date_time=_stamp(export))
                info.compress_type = zipfile.ZIP_STORED
                with zf.open(info, mode='w', force_zip64=True) as dest:
                    for chunk in iter_export_csv(_csv_entries(export)):
                        dest.write(chunk)
        writer.finish()
    except Exception:
        try:
            writer.abort()
        except Exception:
            logger.exception('Could not abort the zip upload of %s', part.key)
        raise
    return writer.size, written


def _claim(export_id: UUID) -> bool:
    """Take the lease on the export's part build (compare-and-swap)."""
    now = timezone.now()
    return bool(
        ReceiptExport.objects.filter(pk=export_id, status=ExportStatus.BUILDING)
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
        .update(lease_until=now + LEASE)
    )


@task(queue_name='receipts')
def build_part(export_id: str, number: int) -> None:
    """Build one zip file of an export, then queue the next, or finish the export. Never raises:
    a failure is counted on the export and retried (`resume_stalled`), then fails it."""
    try:
        export = ReceiptExport.objects.filter(pk=UUID(export_id)).first()
    except ValueError:
        return
    if export is None or export.status != ExportStatus.BUILDING:
        return
    part = export.parts.filter(number=number).first()
    if part is None or part.built_at is not None:
        return
    target = next_part_to_build(export)
    if target != number:
        # Out of order (a duplicate task, a stale retry): hand over to the part that is next.
        if target is not None:
            enqueue_after_commit(build_part, export_id, target)
        return
    if not _claim(export.pk):
        return
    try:
        size, written = _write_part(export, part)
    except Exception as exc:
        logger.exception('Failed to build part %s of export %s', number, export.pk)
        _part_failed(export.pk, exc)
        return
    _part_done(export, part, size, written)


def _part_done(export: ReceiptExport, part: ReceiptExportPart, size: int, written: int) -> None:
    now = timezone.now()
    with transaction.atomic():
        ReceiptExportPart.objects.filter(pk=part.pk, built_at__isnull=True).update(
            built_at=now, size_bytes=size, file_count=written
        )
        still_building = ReceiptExport.objects.filter(
            pk=export.pk, status=ExportStatus.BUILDING
        ).update(attempts=0, progress_at=now, lease_until=None)
    if not still_building:
        # The export ended (failed by another run) while this part was being written.
        try:
            get_storage().delete_object(part.key)
        except Exception:
            logger.exception('Could not delete %s', part.key)
        return
    export.refresh_from_db()
    following = next_part_to_build(export)
    if following is None:
        finish_export(export.pk)
    else:
        enqueue_after_commit(build_part, str(export.pk), following)


def _part_failed(export_id: UUID, exc: Exception) -> None:
    now = timezone.now()
    message = f'{type(exc).__name__}: {exc}'[:255]
    ReceiptExport.objects.filter(pk=export_id, status=ExportStatus.BUILDING).update(
        attempts=F('attempts') + 1,
        last_error=message,
        progress_at=now,
        lease_until=now + RETRY_BACKOFF,
    )
    attempts = ReceiptExport.objects.filter(pk=export_id).values_list('attempts', flat=True).first()
    if attempts is not None and attempts >= MAX_ATTEMPTS:
        fail_export(export_id, message)


def resume_stalled(now: datetime | None = None) -> int:
    """Queue the next part of every build that is not being worked on and has not progressed for
    `STALL_AFTER`: a worker that died (its lease ran out), a retry that is due, a task that was
    never queued. Returns how many were queued. Part tasks are idempotent, so a duplicate is
    harmless."""
    now = now or timezone.now()
    queued = 0
    stalled = ReceiptExport.objects.filter(
        status=ExportStatus.BUILDING, progress_at__lt=now - STALL_AFTER
    ).filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
    for export in stalled:
        try:
            number = next_part_to_build(export)
            if number is None:
                finish_export(export.pk)
            else:
                build_part.enqueue(str(export.pk), number)
            queued += 1
        except Exception:
            logger.exception('Could not resume export %s', export.pk)
    return queued


def finish_export(export_id: UUID) -> bool:
    """Every part is built: the export is ready for `export_retention_days`, what had vanished is
    refunded and the user is told. One transaction around the compare-and-swap, so it happens
    once. Returns whether this call did it."""
    now = timezone.now()
    days = ReceiptsSettings.load().export_retention_days
    with transaction.atomic():
        export = (
            ReceiptExport.objects.select_for_update()
            .filter(pk=export_id, status=ExportStatus.BUILDING)
            .first()
        )
        if export is None:
            return False
        skipped = export.items.filter(skipped=True).count()
        refund = min(skipped * export.price_per_file, export.charged_credits)
        ReceiptExport.objects.filter(pk=export.pk).update(
            status=ExportStatus.READY,
            ready_at=now,
            expires_at=now + timedelta(days=days),
            skipped_count=skipped,
            refunded_credits=refund,
            lease_until=None,
            progress_at=now,
        )
        if refund:
            add_credits(
                export.user,
                refund,
                tx_type=CreditTransactionType.REFUND,
                description=f'Receipts export: {skipped} file(s) no longer there',
                source='receipts',
            )
        export.refresh_from_db()
        _notify_ready(export)
        enqueue_after_commit(send_export_ready_email, str(export.pk))
    return True


def fail_export(export_id: UUID, reason: str = '') -> bool:
    """The build failed for good: refund everything charged, tell the user, delete what was
    written. Returns whether this call did it."""
    with transaction.atomic():
        export = (
            ReceiptExport.objects.select_for_update()
            .filter(pk=export_id, status=ExportStatus.BUILDING)
            .first()
        )
        if export is None:
            return False
        refund = export.charged_credits
        ReceiptExport.objects.filter(pk=export.pk).update(
            status=ExportStatus.FAILED,
            refunded_credits=refund,
            last_error=reason[:255],
            lease_until=None,
        )
        if refund:
            add_credits(
                export.user,
                refund,
                tx_type=CreditTransactionType.REFUND,
                description='Receipts export could not be prepared',
                source='receipts',
            )
        export.refresh_from_db()
        _notify_failed(export)
    discard(export)
    return True


# -- Notifications ------------------------------------------------------------------------------


def _plural(count: int, word: str) -> str:
    return f'{count:,} {word}{"" if count == 1 else "s"}'


def _notify(export: ReceiptExport, title: str, body: str) -> None:
    """In the caller's transaction, in a savepoint of its own: a notice must never undo or fail the
    change it reports. One notice per export (`delete_for_key`, then the new one)."""
    try:
        with transaction.atomic():
            notifications.delete_for_key(export.user_id, notice_key(export.pk), source='receipts')
            notifications.notify(
                export.user_id,
                source='receipts',
                title=title,
                body=body,
                url=export_url_path(export.pk),
                key=notice_key(export.pk),
            )
    except Exception:
        logger.exception('Could not notify user %s about export %s', export.user_id, export.pk)


def _notify_ready(export: ReceiptExport) -> None:
    available = (
        f' Available until {format_date(export.expires_at.date(), "full", PUBLIC)}.'
        if export.expires_at
        else ''
    )
    parts = _plural(export.part_count, 'zip file')
    files = _plural(export.file_count - export.skipped_count, 'receipt file')
    _notify(export, 'Your receipts export is ready', f'{files} in {parts}.{available}')


def _notify_failed(export: ReceiptExport) -> None:
    refunded = (
        f' The {_plural(export.refunded_credits, "credit")} you paid were refunded.'
        if export.refunded_credits
        else ''
    )
    _notify(
        export,
        'Your receipts export could not be prepared',
        f"Something went wrong, so we couldn't make your zip files.{refunded}",
    )


# -- Downloading --------------------------------------------------------------------------------


def part_name(export: ReceiptExport, number: int) -> str:
    """The name a part is downloaded as."""
    created: date = timezone.localdate(export.created_at)
    return part_file_name(created, number, export.part_count)


def part_download_url(export: ReceiptExport, number: int) -> str:
    """A short-lived "Save As" URL for one zip file of a ready export.

    Raises `AccountFrozenError` while the account's storage is paused, `ValidationError` for an
    export that is not ready or a part that does not exist.
    """
    if is_frozen(export.user):
        raise AccountFrozenError()
    if export.status != ExportStatus.READY:
        raise ValidationError('This export is not available to download.')
    part = export.parts.filter(number=number, built_at__isnull=False).first()
    if part is None:
        raise ValidationError('There is no such file in this export.')
    return get_storage().presigned_get_url(
        part.key, part_name(export, number), disposition='attachment'
    )


# -- Ending -------------------------------------------------------------------------------------


def discard(export: ReceiptExport) -> bool:
    """Delete an ended export's objects, then its parts and items rows. Idempotent; a storage
    error is logged and leaves the rows, so `purge_exports` tries again. Returns whether it all
    went."""
    try:
        get_storage().delete_prefix(export_prefix(export.user_id, export.pk))
    except Exception:
        logger.exception('Could not delete the files of export %s', export.pk)
        return False
    ReceiptExportItem.objects.filter(export=export).delete()
    ReceiptExportPart.objects.filter(export=export).delete()
    return True


def _end(export: ReceiptExport, *, to: str, **extra) -> bool:
    won = (
        ReceiptExport.objects.filter(
            pk=export.pk, status__in=[ExportStatus.READY, ExportStatus.FAILED, ExportStatus.EXPIRED]
        )
        .exclude(status=to)
        .update(status=to, **extra)
    )
    if won:
        export.status = to
        discard(export)
    return bool(won)


def delete_export(export: ReceiptExport) -> bool:
    """The user deletes an export: its files go now. No refund. Idempotent (returns whether this
    call did it). An export still being prepared can't be deleted."""
    if export.status == ExportStatus.BUILDING:
        raise ValidationError('This export is still being prepared.')
    if export.status == ExportStatus.DELETED:
        return False
    return _end(export, to=ExportStatus.DELETED)


def expire_exports(now: datetime | None = None) -> int:
    """Daily: exports past `expires_at` are marked expired and their files deleted. Then any ended
    export whose files or rows are still there (a delete that failed) is finished. Returns how many
    were expired."""
    now = now or timezone.now()
    expired = 0
    for export in ReceiptExport.objects.filter(status=ExportStatus.READY, expires_at__lte=now):
        try:
            if _end(export, to=ExportStatus.EXPIRED):
                expired += 1
        except Exception:
            logger.exception('Failed to expire export %s', export.pk)
    leftovers = ReceiptExport.objects.filter(status__in=ENDED_STATUSES).filter(
        Exists(ReceiptExportPart.objects.filter(export=OuterRef('pk')))
        | Exists(ReceiptExportItem.objects.filter(export=OuterRef('pk')))
    )
    for export in leftovers[:200]:
        try:
            discard(export)
        except Exception:
            logger.exception('Failed to clean up export %s', export.pk)
    return expired
