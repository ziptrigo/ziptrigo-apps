"""Exports (issue #315): "Download files" packs the user's receipt files into independent zip files
(about `ReceiptsSettings.export_part_bytes` each, no compression) with one CSV of their data, kept
in S3 for `export_retention_days` and then deleted.

The lifecycle from the moment the rows are written (the build, the refunds, the resilience, the
download and the ending) is shared with the To-do export and lives in
`apps.core.services.exports`, described there; this module is Receipts' side of it: `KIND`, the
public functions that call the engine with it, and what is Receipts' own.

**Flow.** `prepare_export` reads what the filters list and plans it (`export_plan.plan_export`),
which can be a lot of rows, so it never runs under a lock; `quote_export` is its `Quote` (no
writes). `create_export` takes a `Prepared` (or makes one), refuses what it can with no writes, then
in one transaction re-checks only what can have changed since (frozen, the number of files and
the settings: a different list is planned again), raises `ExportCostChangedError` (nothing
charged) when the cost is not the `expected_cost` the user confirmed, writes the export, its parts
and its items, and only then spends the credits (so the billing row is locked for the shortest
time) and queues the first part. It takes no lock on the receipts account: "one export at a time"
is a partial unique constraint (`receipts_export_one_building_per_user`), so a race is an
`IntegrityError` turned into `ExportInProgressError`. **One part is built per task**
(`build_part`, the `receipts` queue). Part 1 is built **last**: it carries `receipts.csv`, which
lists every part's files and must say what really went in.
"""

import logging
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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
from apps.core.datetime_format import PUBLIC, format_date
from apps.core.services import exports as engine
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
    ExportLayout,
    ExportStatus,
    ReceiptCategory,
    ReceiptExport,
    ReceiptExportItem,
    ReceiptExportPart,
    ReceiptsSettings,
    ReceiptStatus,
    Vendor,
)
from .accounts import is_frozen
from .emails import send_export_ready_email
from .errors import (
    AccountFrozenError,
    ExportCostChangedError,
    ExportInProgressError,
)
from .export_plan import (
    CSV_NAME,
    CsvEntry,
    ExportRow,
    Plan,
    iter_export_csv,
    plan_export,
)
from .files import default_name_for
from .filters import UNCATEGORIZED, ReceiptFilters, _statuses, filter_receipts
from .storage import export_key, export_prefix, get_storage

logger = logging.getLogger(__name__)


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


def _write_csv(export: ReceiptExport, zf: zipfile.ZipFile) -> None:
    """`receipts.csv` into part 1's zip, from the database (what was skipped is already marked)."""
    engine.write_zip_entry(zf, export, CSV_NAME, iter_export_csv(_csv_entries(export)))


def _refund(user: User, credits: int, description: str) -> None:
    add_credits(
        user,
        credits,
        tx_type=CreditTransactionType.REFUND,
        description=description,
        source='receipts',
    )


def _can_notify(user: User) -> bool:
    return bool(user.is_active and user.status == User.STATUS_ACTIVE)


@task(queue_name='receipts')
def build_part(export_id: str, number: int) -> None:
    """Build one zip file of an export, then queue the next, or finish the export. Never raises:
    a failure is retried (`resume_stalled`) until the attempts run out, then fails the export."""
    engine.build_part(KIND, export_id, number)


KIND = ExportKind(
    source='receipts',
    label='Receipts export',
    ready_title='Your receipts export is ready',
    failed_title='Your receipts export could not be prepared',
    file_noun='receipt file',
    download_prefix='receipts',
    detail_url_name='receipts:export-detail',
    export_model=ReceiptExport,
    part_model=ReceiptExportPart,
    item_model=ReceiptExportItem,
    build_task=build_part,
    send_ready_email=send_export_ready_email,
    load_config=ReceiptsSettings.load,
    get_storage=get_storage,
    export_prefix=export_prefix,
    is_frozen=is_frozen,
    frozen_error=AccountFrozenError,
    refund=_refund,
    can_notify=_can_notify,
    write_index=_write_csv,
    timezone_of=user_tz,
)

NOTICE_KEY_PREFIX = 'receipts_export:'


def notice_key(export_id: object) -> str:
    return engine.notice_key(KIND, export_id)


def export_url_path(export_id: object) -> str:
    return engine.export_url_path(KIND, export_id)


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
class Prepared:
    """What reading and planning the list came to: the plan, the settings it was made with and the
    quote. Made once per request, outside any lock, and handed from `check_export` to
    `create_export` so a confirm scans the list once."""

    layout: str
    plan: Plan
    config: ReceiptsSettings
    quote: Quote

    @property
    def cost(self) -> int:
        return self.quote.cost


def prepare_export(
    user: User, filters: ReceiptFilters | None = None, layout: str = ExportLayout.YEAR_VENDOR
) -> Prepared:
    """Read what `filters` list and plan it. No writes, no locks (it can be a lot of rows)."""
    layout = clean_layout(layout)
    config = ReceiptsSettings.load()
    plan = plan_export(select_rows(user, filters), layout, config.export_part_bytes)
    quote = Quote(
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
    return Prepared(layout=layout, plan=plan, config=config, quote=quote)


def quote_export(
    user: User, filters: ReceiptFilters | None = None, layout: str = ExportLayout.YEAR_VENDOR
) -> Quote:
    """The number of files, their size, the number of zip files and the cost. No writes."""
    return prepare_export(user, filters, layout).quote


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


def _refuse_early(user: User) -> None:
    """The refusals that need no reading of the list."""
    if is_frozen(user):
        raise AccountFrozenError()
    if ReceiptExport.objects.filter(user=user, status=ExportStatus.BUILDING).exists():
        raise ExportInProgressError()


def _refuse_for(prepared: Prepared, expected_cost: int | None) -> None:
    if not prepared.plan.items:
        raise ValidationError('There are no files to download with these filters.')
    if expected_cost is not None and expected_cost != prepared.cost:
        raise ExportCostChangedError(prepared.cost)


def check_export(
    user: User,
    filters: ReceiptFilters | None,
    layout: str,
    *,
    expected_cost: int | None = None,
    prepared: Prepared | None = None,
) -> Prepared:
    """`create_export`'s refusals, told without locks or writes, so a view can refuse before it
    spends a rate-limit hit. The same exceptions; `create_export` decides again before it writes.
    Returns what it read, to pass on as `prepared` (a caller that already has one passes it in and
    nothing is read again)."""
    layout = clean_layout(layout)
    _refuse_early(user)
    prepared = prepared or prepare_export(user, filters, layout)
    _refuse_for(prepared, expected_cost)
    if prepared.cost and get_balance(user) < prepared.cost:
        raise InsufficientCreditsError()
    return prepared


def _count_files(user: User, filters: ReceiptFilters | None) -> int:
    return filter_receipts(user, filters).exclude(file_key='').order_by().count()


def _recheck(
    user: User,
    filters: ReceiptFilters | None,
    layout: str,
    prepared: Prepared,
    expected_cost: int | None,
) -> Prepared:
    """Inside the transaction, right before anything is written: only what can have changed since
    the list was planned -- the frozen state, the number of files and the settings. If any differs
    the list is planned again (rare), so what is charged is always what is written."""
    if is_frozen(user):
        raise AccountFrozenError()
    config = ReceiptsSettings.load()
    if (
        _count_files(user, filters) != len(prepared.plan.items)
        or config.export_part_bytes != prepared.config.export_part_bytes
        or config.export_price_per_file != prepared.config.export_price_per_file
    ):
        prepared = prepare_export(user, filters, layout)
    _refuse_for(prepared, expected_cost)
    return prepared


def create_export(
    user: User,
    filters: ReceiptFilters | None,
    layout: str,
    *,
    expected_cost: int | None = None,
    prepared: Prepared | None = None,
) -> ReceiptExport:
    """Charge for and start an export of what `filters` list.

    Raises `AccountFrozenError` (storage unpaid), `ExportInProgressError` (one at a time),
    `ValidationError` (an unknown layout, nothing to export), `ExportCostChangedError` (the cost
    is not `expected_cost`; `None` accepts whatever it is, for a client with no one to ask) and
    `InsufficientCreditsError`. A refusal changes and charges nothing. `prepared` is what
    `check_export` returned; it is re-checked, not trusted.

    The export, its parts and its items are written in one transaction with the charge, which is
    the last write (it locks the billing row, so it is held for as short a time as possible). No
    lock on the receipts account is taken.
    """
    layout = clean_layout(layout)
    _refuse_early(user)
    prepared = prepared or prepare_export(user, filters, layout)
    _refuse_for(prepared, expected_cost)
    try:
        with transaction.atomic():
            prepared = _recheck(user, filters, layout, prepared, expected_cost)
            export = _write_export(user, filters, layout, prepared)
            if prepared.cost:
                spend_credits(
                    user,
                    prepared.cost,
                    description=f'Receipts export: {len(prepared.plan.items)} files',
                    source='receipts',
                )
            enqueue_after_commit(build_part, str(export.pk), first_part_to_build(export.part_count))
    except IntegrityError:
        # Two requests passed the early check together: the constraint lets one through.
        if ReceiptExport.objects.filter(user=user, status=ExportStatus.BUILDING).exists():
            raise ExportInProgressError() from None
        raise
    return export


def _write_export(
    user: User, filters: ReceiptFilters | None, layout: str, prepared: Prepared
) -> ReceiptExport:
    plan = prepared.plan
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
        price_per_file=prepared.config.export_price_per_file,
        charged_credits=prepared.cost,
    )
    per_part = Counter(item.part_number for item in plan.items)
    ReceiptExportPart.objects.bulk_create(
        ReceiptExportPart(
            export=export,
            number=number,
            key=export_key(user.pk, export.pk, number),
            file_count=per_part[number],
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


# -- Building, resuming, downloading and ending (the shared lifecycle, `KIND`'s) ---------------


def next_part_to_build(export: ReceiptExport) -> int | None:
    return engine.next_part_to_build(export)


def resume_stalled(now: datetime | None = None) -> int:
    """Queue the next part of every build nobody is working on (`receipts.retry_due`)."""
    return engine.resume_stalled(KIND, now)


def finish_export(export_id: UUID) -> bool:
    """Every part is built: the export is ready, what had vanished is refunded, the user told."""
    return engine.finish_export(KIND, export_id)


def fail_export(export_id: UUID, reason: str = '') -> bool:
    """The build failed for good: refund everything charged, tell the user, delete the files."""
    return engine.fail_export(KIND, export_id, reason)


def part_name(export: ReceiptExport, number: int) -> str:
    """The name a part is downloaded as."""
    return engine.part_name(KIND, export, number)


def part_download(export: ReceiptExport, number: int) -> tuple[str, ReceiptExportPart]:
    """A short-lived "Save As" URL for one zip file of a ready export, with that file's row."""
    return engine.part_download(KIND, export, number)


def part_download_url(export: ReceiptExport, number: int) -> str:
    """`part_download`'s URL alone."""
    return engine.part_download_url(KIND, export, number)


def discard(export: ReceiptExport) -> bool:
    """Delete an ended export's objects, then its parts and items rows."""
    return engine.discard(KIND, export)


def delete_export(export: ReceiptExport) -> bool:
    """The user deletes an export: its files go now. No refund. Idempotent."""
    return engine.delete_export(KIND, export)


def expire_exports(now: datetime | None = None) -> int:
    """Daily: exports past `expires_at` are marked expired and their files deleted."""
    return engine.expire_exports(KIND, now)
