"""Building the "download all" zip lazily (spec section 5): the first request for it claims the
build, a queued task streams every uploaded file straight into a single S3 object at
`transfers/<transfer_id>/all.zip` (spec section 11), and the download page polls a status
partial until it's ready.

Streamed both ways, so this never holds a whole file -- let alone the whole zip -- in memory:
`S3Storage.get_object_stream` reads a source file a chunk at a time, and `_S3MultipartWriter`
below turns `zipfile`'s output into an S3 multipart upload, buffering `PART_SIZE_BYTES` (the same
64 MB the browser's own direct uploads use) at a time before each part goes out -- large enough
that even a many-GB zip stays comfortably under S3's 10,000-part ceiling, without ever buffering
the whole thing. `zipfile.ZipFile` can write to a stream it can't `tell()` (it falls back to
tracking its own offset -- see `_S3MultipartWriter`'s docstring), which is what makes writing
directly into that upload possible: nothing here ever needs the finished zip's total size up
front.

Not billed and not part of `size_bytes` (spec section 5): the zip is a derived, deletable
convenience, not new stored content the sender is being charged for.
"""

import logging
import zipfile
from datetime import timedelta
from typing import IO

from django.db import transaction
from django.db.models import Q
from django.tasks import task
from django.utils import timezone

from ..models import Transfer, ZipStatus
from .storage import PART_SIZE_BYTES, S3Storage, get_storage

logger = logging.getLogger(__name__)

#: Read (and re-write) source files this many bytes at a time.
_COPY_CHUNK_BYTES = 1024 * 1024

#: How long a build may sit `BUILDING` before another request is allowed to re-claim it (a worker
#: that died mid-build, say). Comfortably longer than any real build should ever take, but short
#: enough that a genuinely stuck build doesn't poll forever (see `_try_claim_build`).
BUILD_LEASE = timedelta(minutes=30)


def zip_key(transfer_id: object) -> str:
    """The S3 key for a transfer's "download all" zip (spec section 11)."""
    return f'transfers/{transfer_id}/all.zip'


def _safe_entry_name(name: str) -> str:
    """Flatten a stored `TransferFile.name` into a safe zip entry name: no path separators, so
    nothing inside the zip can be written outside its own directory by a naive/vulnerable
    extractor that doesn't guard against "zip slip" (a name that reached here as `../../etc/x` or
    similar). `services.limits.validate_filename` already strips control characters, but names are
    otherwise unrestricted -- `/`, `\\` and `..` all reach this function unchanged.
    """
    flattened = name.replace('\\', '_').replace('/', '_').lstrip('.')
    return flattened or 'file'


def _dedupe_entry_name(name: str, used: set[str]) -> str:
    """`name`, or `name` renamed to `stem (1).ext`, `stem (2).ext`, ... if it's already in `used`
    -- two `TransferFile`s may share a `name` (nothing stops two uploads of `report.pdf`), and a
    zip can't have two entries with the same path."""
    if name not in used:
        used.add(name)
        return name

    stem, dot, ext = name.rpartition('.')
    if not dot:
        stem, ext = name, ''
    n = 1
    while True:
        candidate = f'{stem} ({n}).{ext}' if ext else f'{stem} ({n})'
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


class _S3MultipartWriter:
    """Write-only, non-seekable file-like object that streams into an S3 multipart upload.

    Buffers up to `part_size` bytes (except the final part, which may be smaller) before
    uploading a part, so the whole zip is never held in memory at once. Deliberately defines no
    `.tell()`: `zipfile.ZipFile.__init__` tries `self.fp.tell()` and, on `AttributeError`, wraps
    the file object in its own offset-tracking shim instead of relying on the file object being
    seekable -- that fallback is what makes writing a zip directly into this work at all, since an
    S3 multipart upload has no concept of seeking backwards to patch up local file headers the way
    a real seekable zip file would.
    """

    def __init__(self, storage: S3Storage, key: str, *, part_size: int = PART_SIZE_BYTES) -> None:
        self._storage = storage
        self._key = key
        self._part_size = part_size
        self._upload_id = storage.create_multipart_upload(key)
        self._buffer = bytearray()
        self._parts: list[dict] = []
        self._part_number = 1

    def write(self, data: bytes) -> int:
        self._buffer += data
        while len(self._buffer) >= self._part_size:
            self._upload_part(bytes(self._buffer[: self._part_size]))
            del self._buffer[: self._part_size]
        return len(data)

    def flush(self) -> None:
        """`zipfile` calls this; there's nothing to do until a part is actually full."""

    def _upload_part(self, chunk: bytes) -> None:
        etag = self._storage.upload_part(self._key, self._upload_id, self._part_number, chunk)
        self._parts.append({'PartNumber': self._part_number, 'ETag': etag})
        self._part_number += 1

    def finish(self) -> None:
        """Flush whatever's left -- even an empty zip needs at least one (possibly empty) part
        -- and complete the multipart upload. Call only once every entry has been written
        successfully; see `abort` for the failure path. Not named `close`, since `zipfile.ZipFile`
        never calls `close()` on a file object it didn't open itself (it was handed one), and a
        method named `close` here could be mistaken for something `zipfile` might call on its own.
        """
        if self._buffer or not self._parts:
            self._upload_part(bytes(self._buffer))
            self._buffer.clear()
        self._storage.complete_multipart_upload(self._key, self._upload_id, self._parts)

    def abort(self) -> None:
        self._storage.abort_multipart_upload(self._key, self._upload_id)


def _copy_stream(src: IO[bytes], dest: IO[bytes]) -> None:
    try:
        while True:
            chunk = src.read(_COPY_CHUNK_BYTES)
            if not chunk:
                return
            dest.write(chunk)
    finally:
        close = getattr(src, 'close', None)
        if close:
            close()


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
        writer = _S3MultipartWriter(storage, key)
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
