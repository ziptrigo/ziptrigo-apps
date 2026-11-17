"""The export lifecycle every product shares (issue #315 for Receipts, moved here for the To-do
export, issue #326): "Download files" packs a user's files into independent zip files (about the
product's `export_part_bytes` each, no compression) plus one index file (Receipts' CSV, To-do's
workbook), kept in S3 for `export_retention_days` and then deleted.

What is a product's own -- which files, how they are named and planned, the quote, the creation
with its refusals and the charge, the index file, the pages -- stays in the product
(`apps.receipts.services.exports`, `apps.todo.services.exports`). What is here is everything from
the moment the rows are written: the build, the money that comes back, the resilience, the
download and the ending. A product describes itself with an `ExportKind` (its models, storage,
task, texts and hooks) and calls these functions with it; its public functions are thin wrappers,
so its views, API and tests don't know about the kind. `apps.core` can't import billing or a
product, so a refund is a hook (`ExportKind.refund`) the product fills with `add_credits`.

**Flow.** `create_export` (the product's) writes the export, its parts and its items in one
transaction with the charge and queues the first part. **One part is built per task**
(`build_part`, the product's queue), each queueing the next, so a long export never holds a worker
in one piece and a restart resumes at the unfinished part. Part 1 is built **last**: it carries
the index file, which lists every part's files and must say what really went in. After the last
part the export is `ready`: `expires_at` is set, the files that had vanished are refunded, and the
user gets a notification and an email.

**Money.** Charged in full at creation (`price_per_file` is stored on the export). A file whose S3
object is gone when its part is built is left out (`skipped` on the item) and refunded at the end.
A build that fails for good refunds everything. Both refunds happen in the very transaction of the
status change that is their compare-and-swap (`building` -> `ready` / `failed`), so each happens
once. Deleting an export, or letting it expire, refunds nothing.

**Resilience.** A part is claimed with a lease (`lease_until`), and **every claim counts an attempt
on the export** (`attempts`, reset by the next part that is built), so a worker that dies without
ever raising (the OOM killer, a deploy) is counted too. A part that raises keeps the lease for
`RETRY_BACKOFF`; `MAX_ATTEMPTS` claims without progress fail the export (and refund it), found by
the claim itself or by `resume_stalled`. The product's retry job calls `resume_stalled`, which
queues the next unbuilt part of a build that is neither leased nor progressing (a dead worker, a
failed part whose backoff has passed, a task that was never queued): a failure does not move
`progress_at`, so the retry comes at the first run after the backoff, once `STALL_AFTER` has passed
since the last progress (the run is every 5 minutes). Every step is idempotent: a part is written
under a deterministic key and marked built with one conditional update; a duplicate task that
finds the part already built after it claimed it lets go, and a part is never deleted from S3
unless its export has ended.

**Ending.** `expire_exports` (the product's daily job), `delete_export` (the user) and a failure
all end an export the same way: status first (the compare-and-swap), then the objects
(`delete_prefix`), then the parts and items rows. The job also finishes any ended export whose
objects or rows are still there (a delete that failed), so a failure to delete never leaks.
Exports are not part of the account's `stored_bytes`: their price covers them.
"""

import logging
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from botocore.exceptions import ClientError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.db.models.functions import Greatest
from django.urls import reverse
from django.utils import timezone

from apps.core.datetime_format import PUBLIC, format_date
from apps.core.models.exports import ExportStatus
from apps.core.services import notifications
from apps.core.services.export_errors import ExportPartMissingError
from apps.core.services.export_names import part_file_name
from apps.core.services.queue import enqueue_after_commit
from apps.core.services.zip_stream import S3MultipartWriter, copy_stream

logger = logging.getLogger(__name__)

#: Times a worker may take a part without any part being built before the export fails (and is
#: refunded): counted at the claim, so a worker that dies without raising counts too.
MAX_ATTEMPTS = 3

#: How long a worker may hold a part. A part is at most about `export_part_bytes` read from S3 and
#: written back, a few minutes; this is the time after which a dead worker's part is taken over.
LEASE = timedelta(minutes=15)

#: How long a part that raised waits before it is tried again (it keeps the lease that long).
RETRY_BACKOFF = timedelta(minutes=2)

#: `resume_stalled` re-queues a build with no lease that has not progressed for this long.
STALL_AFTER = timedelta(minutes=5)

#: What the zip writer buffers before it uploads an S3 part (S3 needs at least 5 MB).
WRITER_PART_BYTES = 16 * 1024 * 1024

ENDED_STATUSES = (ExportStatus.FAILED, ExportStatus.EXPIRED, ExportStatus.DELETED)

# The export, part and item rows are each product's own model (a subclass of
# `apps.core.models.exports.Base*`); they are typed loosely here, since a type that names the
# product's model can't be written in a module that must not import it.
type ExportRecord = Any
type PartRecord = Any


@dataclass(frozen=True, slots=True)
class Quote:
    """What an export of the current selection would be (the product's `quote_export`)."""

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


@dataclass(frozen=True, slots=True)
class ExportKind:
    """What the lifecycle needs to know about a product's export."""

    source: str
    """The billing and notification `source` (`'receipts'`, `'todo'`)."""

    label: str
    """What its charges are described as: `'Receipts export'` -> "Receipts export: 3 files"."""

    ready_title: str
    failed_title: str
    """The in-app notice's titles ("Your receipts export is ready")."""

    file_noun: str
    """One file, as the notice counts them (`'receipt file'`)."""

    download_prefix: str
    """What a zip is downloaded as: `receipts` -> `receipts-2026-11-16-part-01-of-05.zip`."""

    detail_url_name: str
    """The URL name of one export's page (takes the export's id): where the notice and the email
    link."""

    export_model: Any
    part_model: Any
    item_model: Any
    """The product's `*Export`, `*ExportPart` and `*ExportItem` models (subclasses of
    `apps.core.models.exports.Base*`), typed loosely: a type that names them can't be written in
    a module that must not import a product."""

    build_task: Any
    """The product's `build_part(export_id: str, number: int)` task (on the product's queue)."""

    send_ready_email: Any
    """The product's `send_export_ready_email(export_id: str)` task."""

    load_config: Callable[[], Any]
    """The product's settings row (`export_retention_days` is read from it)."""

    get_storage: Callable[[], Any]
    export_prefix: Callable[[Any, Any], str]
    """`(user_id, export_id)` -> every object key of one export, with the trailing slash."""

    is_frozen: Callable[[Any], bool]
    """Whether a user's storage is paused (takes the `User`)."""

    frozen_error: Callable[[], Exception]
    """The product's `AccountFrozenError`, raised when a frozen account downloads a part."""

    refund: Callable[[Any, int, str], None]
    """`(user, credits, description)`: put credits back (`add_credits(tx_type=REFUND)`)."""

    can_notify: Callable[[Any], bool]
    """Whether a user can sign in (active account): none of the notices go to one who can't."""

    write_index: Callable[[ExportRecord, zipfile.ZipFile], None]
    """Write the index file into part 1's zip (`write_zip_entry` writes one entry)."""


def notice_key(kind: ExportKind, export_id: object) -> str:
    """The notification key (`receipts_export:<id>`), so one export has one notice, the latest."""
    return f'{kind.source}_export:{export_id}'


def export_url_path(kind: ExportKind, export_id: object) -> str:
    return reverse(kind.detail_url_name, args=[export_id])


# -- Building -----------------------------------------------------------------------------------


def build_order(part_count: int) -> list[int]:
    """The order parts are built in: 2, 3, ... then 1 last (it holds the index of everything)."""
    return [*range(2, part_count + 1), 1] if part_count else []


def first_part_to_build(part_count: int) -> int:
    return build_order(part_count)[0]


def next_part_to_build(export: ExportRecord) -> int | None:
    built = set(export.parts.filter(built_at__isnull=False).values_list('number', flat=True))
    for number in build_order(export.part_count):
        if number not in built:
            return number
    return None


def is_missing(exc: ClientError) -> bool:
    return exc.response.get('Error', {}).get('Code', '') in ('NoSuchKey', '404', 'NotFound')


def zip_stamp(export: ExportRecord) -> tuple[int, int, int, int, int, int]:
    """The time every entry of the export's zips carries (when it was asked for)."""
    return export.created_at.timetuple()[:6]


def write_zip_entry(
    zf: zipfile.ZipFile, export: ExportRecord, name: str, chunks: Iterable[bytes]
) -> None:
    """Write one stored (not compressed) entry of `chunks` (bytes) into `zf`."""
    info = zipfile.ZipInfo(name, date_time=zip_stamp(export))
    info.compress_type = zipfile.ZIP_STORED
    with zf.open(info, mode='w', force_zip64=True) as dest:
        for chunk in chunks:
            dest.write(chunk)


def write_part(kind: ExportKind, export: ExportRecord, part: PartRecord) -> tuple[int, int]:
    """Write one part's zip to S3. Returns its size and how many files went in."""
    storage = kind.get_storage()
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
                    if not is_missing(exc):
                        raise
                    gone.append(item.pk)
                    continue
                info = zipfile.ZipInfo(item.path, date_time=zip_stamp(export))
                info.compress_type = zipfile.ZIP_STORED
                with zf.open(info, mode='w', force_zip64=True) as dest:
                    copy_stream(stream, dest)
                written += 1
            if gone:
                kind.item_model.objects.filter(pk__in=gone).update(skipped=True)
            if part.number == 1:
                # Last, and from the database: what was skipped anywhere is already marked.
                kind.write_index(export, zf)
        writer.finish()
    except Exception:
        try:
            writer.abort()
        except Exception:
            logger.exception('Could not abort the zip upload of %s', part.key)
        raise
    return writer.size, written


def claim(kind: ExportKind, export_id: UUID) -> bool:
    """Take the lease on the export's part build (compare-and-swap) and count the claim as an
    attempt: it is reset when a part is built, so a worker that keeps dying without raising runs
    the export out of attempts. False while someone holds the lease or the attempts are spent."""
    now = timezone.now()
    return bool(
        kind.export_model.objects.filter(
            pk=export_id, status=ExportStatus.BUILDING, attempts__lt=MAX_ATTEMPTS
        )
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
        .update(lease_until=now + LEASE, attempts=F('attempts') + 1)
    )


def release(kind: ExportKind, export_id: UUID) -> None:
    """Let go of a lease that was claimed for nothing (the part was already built): the claim does
    not count as an attempt."""
    kind.export_model.objects.filter(pk=export_id, status=ExportStatus.BUILDING).update(
        lease_until=None, attempts=Greatest(F('attempts') - 1, 0)
    )


def give_up_if_exhausted(kind: ExportKind, export_id: UUID) -> bool:
    """Fail (and refund) a build that was claimed `MAX_ATTEMPTS` times without any part being built
    and that nobody holds now. Returns whether it did."""
    now = timezone.now()
    exhausted = (
        kind.export_model.objects.filter(
            pk=export_id, status=ExportStatus.BUILDING, attempts__gte=MAX_ATTEMPTS
        )
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
        .exists()
    )
    return exhausted and fail_export(kind, export_id, 'The build was interrupted too many times.')


def build_part(kind: ExportKind, export_id: str, number: int) -> None:
    """Build one zip file of an export, then queue the next, or finish the export. The body of the
    product's `build_part` task. Never raises: a failure is retried (`resume_stalled`) until the
    attempts run out, then fails the export."""
    try:
        export = kind.export_model.objects.filter(pk=UUID(export_id)).first()
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
            enqueue_after_commit(kind.build_task, export_id, target)
        return
    if not claim(kind, export.pk):
        give_up_if_exhausted(kind, export.pk)
        return
    # The part may have been built between the read above and the claim (a duplicate task: the
    # other worker's lease ended when it finished). Look again, now that the lease is ours.
    part.refresh_from_db()
    if part.built_at is not None:
        release(kind, export.pk)
        return
    try:
        size, written = write_part(kind, export, part)
    except Exception as exc:
        logger.exception('Failed to build part %s of export %s', number, export.pk)
        part_failed(kind, export.pk, exc)
        return
    part_done(kind, export, part, size, written)


def part_done(
    kind: ExportKind, export: ExportRecord, part: PartRecord, size: int, written: int
) -> None:
    now = timezone.now()
    with transaction.atomic():
        won = kind.part_model.objects.filter(pk=part.pk, built_at__isnull=True).update(
            built_at=now, size_bytes=size, file_count=written
        )
        building = kind.export_model.objects.filter(pk=export.pk, status=ExportStatus.BUILDING)
        if won:
            still_building = building.update(attempts=0, progress_at=now, lease_until=None)
        else:
            # Another run built it first (it wrote the same object under the same key): not
            # progress of ours, and the winner carries on the chain.
            building.update(lease_until=None)
            still_building = 0
    if not won or not still_building:
        ended = kind.export_model.objects.filter(pk=export.pk, status__in=ENDED_STATUSES).exists()
        if ended:
            # The export ended (failed by another run, deleted) while this part was being
            # written. Never when it is ready or still building: then the object is the part.
            try:
                kind.get_storage().delete_object(part.key)
            except Exception:
                logger.exception('Could not delete %s', part.key)
        return
    export.refresh_from_db()
    following = next_part_to_build(export)
    if following is None:
        finish_export(kind, export.pk)
    else:
        enqueue_after_commit(kind.build_task, str(export.pk), following)


def part_failed(kind: ExportKind, export_id: UUID, exc: Exception) -> None:
    """The claim already counted this try. The lease is kept for `RETRY_BACKOFF` (the retry waits
    for it) and `progress_at` is left alone: a failure is not progress, so the backoff is the only
    wait `resume_stalled` adds."""
    now = timezone.now()
    message = f'{type(exc).__name__}: {exc}'[:255]
    kind.export_model.objects.filter(pk=export_id, status=ExportStatus.BUILDING).update(
        last_error=message, lease_until=now + RETRY_BACKOFF
    )
    attempts = (
        kind.export_model.objects.filter(pk=export_id).values_list('attempts', flat=True).first()
    )
    if attempts is not None and attempts >= MAX_ATTEMPTS:
        fail_export(kind, export_id, message)


def resume_stalled(kind: ExportKind, now: datetime | None = None) -> int:
    """Queue the next part of every build that is not being worked on and has not progressed for
    `STALL_AFTER`: a worker that died (its lease ran out), a retry whose backoff has passed, a task
    that was never queued. A build whose attempts are spent (a part claimed `MAX_ATTEMPTS` times
    with no progress) is failed and refunded instead. Returns how many were handled. Part tasks are
    idempotent, so a duplicate is harmless."""
    now = now or timezone.now()
    handled = 0
    stalled = kind.export_model.objects.filter(
        status=ExportStatus.BUILDING, progress_at__lt=now - STALL_AFTER
    ).filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
    for export in stalled:
        try:
            if export.attempts >= MAX_ATTEMPTS:
                fail_export(kind, export.pk, 'The build was interrupted too many times.')
            else:
                number = next_part_to_build(export)
                if number is None:
                    finish_export(kind, export.pk)
                else:
                    kind.build_task.enqueue(str(export.pk), number)
            handled += 1
        except Exception:
            logger.exception('Could not resume export %s', export.pk)
    return handled


def finish_export(kind: ExportKind, export_id: UUID) -> bool:
    """Every part is built: the export is ready for `export_retention_days`, what had vanished is
    refunded and the user is told. One transaction around the compare-and-swap, so it happens
    once. Returns whether this call did it."""
    now = timezone.now()
    days = kind.load_config().export_retention_days
    with transaction.atomic():
        export = (
            kind.export_model.objects.select_for_update()
            .filter(pk=export_id, status=ExportStatus.BUILDING)
            .first()
        )
        if export is None:
            return False
        skipped = export.items.filter(skipped=True).count()
        refund = min(skipped * export.price_per_file, export.charged_credits)
        kind.export_model.objects.filter(pk=export.pk).update(
            status=ExportStatus.READY,
            ready_at=now,
            expires_at=now + timedelta(days=days),
            skipped_count=skipped,
            refunded_credits=refund,
            lease_until=None,
            progress_at=now,
        )
        if refund:
            kind.refund(export.user, refund, f'{kind.label}: {skipped} file(s) no longer there')
        export.refresh_from_db()
        _notify_ready(kind, export)
        enqueue_after_commit(kind.send_ready_email, str(export.pk))
    return True


def fail_export(kind: ExportKind, export_id: UUID, reason: str = '') -> bool:
    """The build failed for good: refund everything charged, tell the user, delete what was
    written. Returns whether this call did it."""
    with transaction.atomic():
        export = (
            kind.export_model.objects.select_for_update()
            .filter(pk=export_id, status=ExportStatus.BUILDING)
            .first()
        )
        if export is None:
            return False
        refund = export.charged_credits
        kind.export_model.objects.filter(pk=export.pk).update(
            status=ExportStatus.FAILED,
            refunded_credits=refund,
            last_error=reason[:255],
            lease_until=None,
        )
        if refund:
            kind.refund(export.user, refund, f'{kind.label} could not be prepared')
        export.refresh_from_db()
        _notify_failed(kind, export)
    discard(kind, export)
    return True


# -- Notifications ------------------------------------------------------------------------------


def plural(count: int, word: str) -> str:
    return f'{count:,} {word}{"" if count == 1 else "s"}'


def _notify(kind: ExportKind, export: ExportRecord, title: str, body: str) -> None:
    """In the caller's transaction, in a savepoint of its own: a notice must never undo or fail the
    change it reports. One notice per export (`delete_for_key`, then the new one). None for an
    account that can't sign in."""
    if not kind.can_notify(export.user):
        return
    try:
        with transaction.atomic():
            notifications.delete_for_key(
                export.user_id, notice_key(kind, export.pk), source=kind.source
            )
            notifications.notify(
                export.user_id,
                source=kind.source,
                title=title,
                body=body,
                url=export_url_path(kind, export.pk),
                key=notice_key(kind, export.pk),
            )
    except Exception:
        logger.exception('Could not notify user %s about export %s', export.user_id, export.pk)


def _notify_ready(kind: ExportKind, export: ExportRecord) -> None:
    available = (
        f' Available until {format_date(export.expires_at.date(), "full", PUBLIC)}.'
        if export.expires_at
        else ''
    )
    parts = plural(export.part_count, 'zip file')
    files = plural(export.file_count - export.skipped_count, kind.file_noun)
    _notify(kind, export, kind.ready_title, f'{files} in {parts}.{available}')


def _notify_failed(kind: ExportKind, export: ExportRecord) -> None:
    refunded = (
        f' The {plural(export.refunded_credits, "credit")} you paid were refunded.'
        if export.refunded_credits
        else ''
    )
    _notify(
        kind,
        export,
        kind.failed_title,
        f"Something went wrong, so we couldn't make your zip files.{refunded}",
    )


# -- Downloading --------------------------------------------------------------------------------


def part_name(kind: ExportKind, export: ExportRecord, number: int) -> str:
    """The name a part is downloaded as."""
    created: date = timezone.localdate(export.created_at)
    return part_file_name(kind.download_prefix, created, number, export.part_count)


def part_download(kind: ExportKind, export: ExportRecord, number: int) -> tuple[str, PartRecord]:
    """A short-lived "Save As" URL for one zip file of a ready export, with that file's row.

    Raises the product's `AccountFrozenError` while the account's storage is paused,
    `ValidationError` for an export that is not ready and `ExportPartMissingError` (a
    `ValidationError`) for a part that does not exist or is not built.
    """
    if kind.is_frozen(export.user):
        raise kind.frozen_error()
    if export.status != ExportStatus.READY:
        raise ValidationError('This export is not available to download.')
    part = export.parts.filter(number=number, built_at__isnull=False).first()
    if part is None:
        raise ExportPartMissingError()
    url = kind.get_storage().presigned_get_url(
        part.key, part_name(kind, export, number), disposition='attachment'
    )
    return url, part


def part_download_url(kind: ExportKind, export: ExportRecord, number: int) -> str:
    """`part_download`'s URL alone."""
    return part_download(kind, export, number)[0]


# -- Ending -------------------------------------------------------------------------------------


def discard(kind: ExportKind, export: ExportRecord) -> bool:
    """Delete an ended export's objects, then its parts and items rows. Idempotent; a storage
    error is logged and leaves the rows, so the daily job tries again. Returns whether it all
    went."""
    try:
        kind.get_storage().delete_prefix(kind.export_prefix(export.user_id, export.pk))
    except Exception:
        logger.exception('Could not delete the files of export %s', export.pk)
        return False
    kind.item_model.objects.filter(export=export).delete()
    kind.part_model.objects.filter(export=export).delete()
    return True


def end(kind: ExportKind, export: ExportRecord, *, to: str, **extra) -> bool:
    won = (
        kind.export_model.objects.filter(
            pk=export.pk, status__in=[ExportStatus.READY, ExportStatus.FAILED, ExportStatus.EXPIRED]
        )
        .exclude(status=to)
        .update(status=to, **extra)
    )
    if won:
        export.status = to
        discard(kind, export)
    return bool(won)


def delete_export(kind: ExportKind, export: ExportRecord) -> bool:
    """The user deletes an export: its files go now. No refund. Idempotent (returns whether this
    call did it). An export still being prepared can't be deleted."""
    if export.status == ExportStatus.BUILDING:
        raise ValidationError('This export is still being prepared.')
    if export.status == ExportStatus.DELETED:
        return False
    return end(kind, export, to=ExportStatus.DELETED)


def expire_exports(kind: ExportKind, now: datetime | None = None) -> int:
    """Daily: exports past `expires_at` are marked expired and their files deleted. Then any ended
    export whose files or rows are still there (a delete that failed) is finished. Returns how many
    were expired."""
    now = now or timezone.now()
    expired = 0
    for export in kind.export_model.objects.filter(status=ExportStatus.READY, expires_at__lte=now):
        try:
            if end(kind, export, to=ExportStatus.EXPIRED):
                expired += 1
        except Exception:
            logger.exception('Failed to expire export %s', export.pk)
    leftovers = kind.export_model.objects.filter(status__in=ENDED_STATUSES).filter(
        Exists(kind.part_model.objects.filter(export=OuterRef('pk')))
        | Exists(kind.item_model.objects.filter(export=OuterRef('pk')))
    )
    for export in leftovers[:200]:
        try:
            discard(kind, export)
        except Exception:
            logger.exception('Failed to clean up export %s', export.pk)
    return expired
