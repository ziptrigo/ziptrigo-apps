"""Building the "download all" zip lazily (spec section 5): the first request for it claims the
build, a queued task streams every uploaded file straight into a single S3 object at
`transfers/<transfer_id>/all.zip` (spec section 11), and the download page polls a status
partial until it's ready.

Streamed both ways, so this never holds a whole file -- let alone the whole zip -- in memory
(the streaming helpers moved to `apps.core.services.zip_stream` so Receipts' exports share them):
`S3Storage.get_object_stream` reads a source file a chunk at a time, and `S3MultipartWriter`
turns `zipfile`'s output into an S3 multipart upload, buffering `PART_SIZE_BYTES` (the same
64 MB the browser's own direct uploads use) at a time before each part goes out -- large enough
that even a many-GB zip stays comfortably under S3's 10,000-part ceiling, without ever buffering
the whole thing.

Not billed and not part of `size_bytes` (spec section 5): the zip is a derived, deletable
convenience, not new stored content the sender is being charged for.
"""

import logging
import zipfile
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.tasks import task
from django.utils import timezone

from apps.core.services.zip_stream import S3MultipartWriter as _S3MultipartWriter
from apps.core.services.zip_stream import copy_stream as _copy_stream
from apps.core.services.zip_stream import dedupe_entry_name as _dedupe_entry_name
from apps.core.services.zip_stream import safe_entry_name as _safe_entry_name

from ..models import Transfer, ZipStatus
from .storage import PART_SIZE_BYTES, get_storage

logger = logging.getLogger(__name__)

#: How long a build may sit `BUILDING` before another request is allowed to re-claim it (a worker
#: that died mid-build, say). Comfortably longer than any real build should ever take, but short
#: enough that a genuinely stuck build doesn't poll forever (see `_try_claim_build`).
BUILD_LEASE = timedelta(minutes=30)


def zip_key(transfer_id: object) -> str:
    """The S3 key for a transfer's "download all" zip (spec section 11)."""
    return f'transfers/{transfer_id}/all.zip'


def _try_claim_build(transfer: Transfer) -> bool:
    """Atomically flip `zip_status` to `BUILDING`, so that of any number of concurrent requests
    for the same transfer's zip, only one actually enqueues a build (spec section 5). Returns
    whether *this* call won the claim.

    Also reclaims a `BUILDING` row whose `zip_build_started_at` is older than `BUILD_LEASE`: a
    worker that died mid-build (or a task that got redelivered and is still running when the
    process is killed) would otherwise leave `zip_status` stuck at `BUILDING` forever, with
    nothing to move it back to `NONE`/`FAILED` and the download page polling indefinitely.
    """
    now = timezone.now()
    stale_cutoff = now - BUILD_LEASE
    updated = Transfer.objects.filter(
        Q(zip_status__in=[ZipStatus.NONE, ZipStatus.FAILED])
        | Q(zip_status=ZipStatus.BUILDING, zip_build_started_at__lte=stale_cutoff),
        pk=transfer.pk,
    ).update(zip_status=ZipStatus.BUILDING, zip_build_started_at=now)
    return updated == 1


def ensure_zip_build_started(transfer: Transfer) -> None:
    """Called from the download page the moment "download all" is asked for: claim and enqueue
    the build if one isn't already in flight or done, otherwise do nothing (a concurrent request,
    or a page reload while "preparing" is still showing, must not start a second build)."""
    if _try_claim_build(transfer):
        transfer_id = str(transfer.id)
        transaction.on_commit(lambda: build_zip.enqueue(transfer_id))


@task
def build_zip(transfer_id: str) -> None:
    """Build `transfer`'s "download all" zip and mark it `READY` (or `FAILED`, if anything goes
    wrong -- retriable by simply asking for the zip again, which re-claims it from `FAILED`).

    Only ever does real work for the request that actually claimed the build (`zip_status` was
    `BUILDING` because *this* task's own `ensure_zip_build_started` call put it there): an
    at-least-once redelivery of the same task, or one running for a transfer whose build already
    finished or was reset, is a no-op.
    """
    try:
        transfer = Transfer.objects.get(id=transfer_id)
    except Transfer.DoesNotExist:
        return
    if transfer.zip_status != ZipStatus.BUILDING:
        return
    if transfer.is_ended:
        # Ended (deleted/expired) while this was queued: its files are already gone or going, so
        # there's nothing left to zip. Leave it as a (retriable, but pointless) failure rather
        # than block on files that no longer exist.
        Transfer.objects.filter(pk=transfer.pk, zip_status=ZipStatus.BUILDING).update(
            zip_status=ZipStatus.FAILED
        )
        return

    storage = get_storage()
    key = zip_key(transfer.id)
    files = list(transfer.files.filter(uploaded=True).order_by('name'))

    writer: _S3MultipartWriter | None = None
    try:
        # The writer's own constructor (`create_multipart_upload`) is inside this `try` too: if
        # *that* fails, there's nothing yet to abort, but `zip_status` must still come back out of
        # `BUILDING` -- left outside the `try`, a failure here would leave it stuck exactly like
        # the "stuck in BUILDING forever" problem `BUILD_LEASE` otherwise guards against, just
        # immediately instead of after a crash.
        writer = _S3MultipartWriter(storage, key, part_size=PART_SIZE_BYTES)
        # `_S3MultipartWriter` deliberately doesn't structurally match any of `ZipFile`'s typed
        # overloads (no `tell`/`seekable` -- see its docstring for why that's the point), so `ty`
        # can't match this call against them.
        with zipfile.ZipFile(  # ty: ignore[no-matching-overload]
            writer, mode='w', compression=zipfile.ZIP_STORED, allowZip64=True
        ) as zf:
            used_names: set[str] = set()
            for file in files:
                entry_name = _dedupe_entry_name(_safe_entry_name(file.name), used_names)
                date_time = (file.created_at or timezone.now()).timetuple()[:6]
                info = zipfile.ZipInfo(entry_name, date_time=date_time)
                info.compress_type = zipfile.ZIP_STORED
                with zf.open(info, mode='w', force_zip64=True) as dest:
                    _copy_stream(storage.get_object_stream(file.storage_key), dest)
        writer.finish()
    except Exception:
        logger.exception('Failed to build zip for transfer %s', transfer.pk)
        if writer is not None:
            try:
                writer.abort()
            except Exception:
                logger.exception('Failed to abort zip upload for transfer %s', transfer.pk)
        Transfer.objects.filter(pk=transfer.pk, zip_status=ZipStatus.BUILDING).update(
            zip_status=ZipStatus.FAILED
        )
        return

    updated = Transfer.objects.filter(pk=transfer.pk, zip_status=ZipStatus.BUILDING).update(
        zip_status=ZipStatus.READY, zip_key=key
    )
    fresh = Transfer.objects.get(pk=transfer.pk)
    if updated == 0 or fresh.is_ended or fresh.files_deleted_at:
        # Either this call's own status flip lost the race (a concurrent reset/reclaim already
        # moved `zip_status` away from `BUILDING`), or the transfer ended while this build was
        # running: `services.lifecycle.delete_transfer_files`'s prefix sweep runs at end-of-life,
        # but an in-progress multipart upload isn't an object yet, so a sweep that ran *before*
        # `writer.finish()` completed it above finds nothing here to delete -- leaving this
        # freshly-written object an orphan nobody else will ever clean up. Delete it now (harmless
        # if it's already gone) and make sure `zip_status` doesn't keep claiming it's ready.
        storage.delete_object(key)
        Transfer.objects.filter(pk=transfer.pk, zip_status=ZipStatus.READY, zip_key=key).update(
            zip_status=ZipStatus.NONE, zip_key=''
        )
