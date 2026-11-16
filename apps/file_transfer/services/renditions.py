"""Downsized previews of images and PDFs (issue #311, the owner's redesign): what the download
page's Preview shows instead of the file itself, so that previewing never hands over the real bytes
-- getting those is "View full image" / "View full document", which counts as a download.

**What exists.** An image has one WebP (longer side at most 1600 px), a PDF one WebP per page (the
first `MAX_PREVIEW_PAGES`), kept in the file transfer bucket under the transfer's own prefix at
`transfers/<transfer id>/previews/<file id>/<n>.webp` (`preview_key`), so they live and die with
the transfer. **Not billed and not part of `size_bytes`**, like the "download all" zip: a derived,
deletable convenience. `rendition_render` does the reducing.

**Built lazily, on the first Preview click, then reused.** The state is on the file
(`TransferFile.preview_status`, see `PreviewStatus`): `ensure_build_started` claims the build with
one conditional `UPDATE` (`none` -> `building`; a `failed` one after `RETRY_COOLDOWN`, 5 minutes, so a file
that keeps making the child fail can't occupy the worker once per click; or a `building` older than
`BUILD_LEASE`, a worker that died) -- as `services.zip` does -- and queues `build_preview`; the dialog polls the
status endpoint (`views.download.preview_status`) until it is `ready`. Everything is idempotent: a
duplicate task finds the file no longer `building` and does nothing.

**Untrusted input, bounded resources.** The bytes come from a stranger, so decoding runs in a child
process under a memory, CPU and wall-clock budget (`render_in_child`; settings
`FILE_TRANSFER_PREVIEW_*`), exactly the pattern of Receipts' document preparation
(`apps.receipts.services.documents`, whose docstring has the long reasoning): the child is
`rendition_render.py` run as a script with the bytes on stdin and JSON on stdout, in its own
session with a minimal environment, as an unprivileged user when the worker is root, asking the OOM
killer to pick it first. **A resource budget, not an isolation boundary.** Hard caps in front of
it: a file over `MAX_SOURCE_BYTES` (50 MB) is never read, and an image whose header claims more
than 100 megapixels is refused before it is decoded. Those and any file the child calls unreadable
or over the budget are **`unavailable`** (a verdict about the file, kept: the dialog offers only
View full); anything that says nothing about the file (the child killed from outside, a timeout, an
S3 error) is **`failed`**, which the next click tries again.

**Queue.** `build_preview` runs on the `default` queue: each build is bounded (the budget above,
at most `FILE_TRANSFER_PREVIEW_TIMEOUT_SECONDS`) and happens once per file. The risk -- a sender
who uploads many worst-case PDFs and a visitor who clicks them all would occupy the `default`
worker, which also sends file transfer's emails -- is accepted for now; if it ever bites, give it
its own queue like Receipts has (a `previews` entry in `TASKS['default']['QUEUES']` and a
`db_worker --queue-name previews` in `supervise.py` and `docker-compose.yml`).

**Deletion.** (a) Whenever the file's objects go: `lifecycle.delete_transfer_files` deletes the
whole transfer prefix (expiry, delete now, takedown, the suspension grace period, a draft's abort)
and resets the states; removing a single file (`uploads.remove_file`, a file request's
`_remove`) calls `delete_renditions`. (b) A build racing either re-checks at its end and deletes
what it wrote (the zip's pattern). (c) The daily `file_transfer.purge_previews` job
(`purge_unused`) deletes the pages of a file nobody previewed for `PREVIEW_CACHE_DAYS` and resets
it to `none`, so the next click builds them again.
"""

import base64
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.tasks import task
from django.utils import timezone

from apps.core.services.storage import ObjectTooLargeError

from ..models import PreviewStatus, Transfer, TransferFile
from . import preview, rendition_render
from .rendition_render import MAX_IMAGE_PIXELS, MAX_PREVIEW_PAGES, STARTED_MARKER
from .storage import S3Storage, get_storage, transfer_prefix

__all__ = [
    'BUILD_LEASE',
    'MAX_IMAGE_PIXELS',
    'MAX_PREVIEW_PAGES',
    'MAX_SOURCE_BYTES',
    'PREVIEW_CACHE_DAYS',
    'RenditionFailed',
    'RenditionUnavailable',
    'build_preview',
    'delete_renditions',
    'ensure_build_started',
    'has_renditions',
    'preview_key',
    'preview_prefix',
    'purge_unused',
    'render_pages',
    'state_of',
    'touch',
]

logger = logging.getLogger(__name__)

#: A file over this is never read for a preview (it becomes `unavailable` at the click).
MAX_SOURCE_BYTES = 50 * 1024 * 1024
#: How long a file's pages are kept when nobody previews it (the daily `purge_previews` job).
PREVIEW_CACHE_DAYS = 14
#: How long a build may sit `building` before another click may claim it again.
BUILD_LEASE = timedelta(minutes=10)
#: How long after a failed build the next click may try again. A transient failure is not a verdict,
#: but a file that keeps making the child fail (a hang until the timeout, say) must not be able to
#: occupy the worker with one 60 second build per click: this bounds it to one per file per cooldown.
RETRY_COOLDOWN = timedelta(minutes=5)
#: How often showing a page may write `preview_last_used_at` for the same file.
LAST_USED_RESOLUTION = timedelta(hours=1)

#: The content type of every page.
CONTENT_TYPE = 'image/webp'

_KIND_BY_PREVIEW_KIND = {
    preview.PreviewKind.IMAGE: rendition_render.IMAGE,
    preview.PreviewKind.PDF: rendition_render.PDF,
}


class RenditionUnavailable(Exception):
    """The file can't be reduced (unreadable, or over the limits): a verdict about the file."""


class RenditionFailed(Exception):
    """Reducing failed in a way that says nothing about the file: try again later."""


def preview_prefix(transfer_id: object, file_id: object) -> str:
    """The key prefix of one file's pages (under the transfer's own prefix)."""
    return f'{transfer_prefix(transfer_id)}previews/{file_id}/'


def preview_key(transfer_id: object, file_id: object, page: int) -> str:
    """The key of page `page` (1-based) of a file's downsized preview."""
    return f'{preview_prefix(transfer_id, file_id)}{page}.webp'


def has_renditions(file: TransferFile) -> bool:
    """Whether this file is shown as downsized pages (an image or a PDF) rather than as text."""
    return preview.kind_of(file.preview_type) in _KIND_BY_PREVIEW_KIND


# -- The state machine ---------------------------------------------------------------------------


def ensure_build_started(file: TransferFile) -> bool:
    """The first Preview click (or a retry): claim the build and queue it. Returns whether this
    call claimed it. Does nothing for a `ready`, `unavailable` or already `building` file (a
    concurrent request, or a reload while "Preparing preview" shows, must not start a second
    build). A file over `MAX_SOURCE_BYTES` becomes `unavailable` without a worker."""
    if file.size > MAX_SOURCE_BYTES:
        TransferFile.objects.filter(
            pk=file.pk, preview_status__in=[PreviewStatus.NONE, PreviewStatus.FAILED]
        ).update(preview_status=PreviewStatus.UNAVAILABLE)
        return False
    now = timezone.now()
    claimed = TransferFile.objects.filter(
        Q(preview_status=PreviewStatus.NONE)
        | Q(preview_status=PreviewStatus.FAILED, preview_started_at__isnull=True)
        | Q(preview_status=PreviewStatus.FAILED, preview_started_at__lte=now - RETRY_COOLDOWN)
        | Q(preview_status=PreviewStatus.BUILDING, preview_started_at__lte=now - BUILD_LEASE),
        pk=file.pk,
        uploaded=True,
    ).update(preview_status=PreviewStatus.BUILDING, preview_started_at=now)
    if claimed:
        file_id = str(file.pk)
        transaction.on_commit(lambda: build_preview.enqueue(file_id))
    return bool(claimed)


def state_of(file: TransferFile) -> dict:
    """What the status endpoint tells the dialog: `status` is `preparing` (build in flight or just
    claimed), `ready` (with the page count), `failed` (try again), `unavailable` (no preview of
    this file; View full only) or `none` (nothing built, nothing claimed)."""
    file.refresh_from_db(
        fields=['preview_status', 'preview_pages', 'preview_total_pages', 'preview_started_at']
    )
    status = file.preview_status
    return {
        'status': 'preparing' if status == PreviewStatus.BUILDING else status,
        'pages': file.preview_pages if status == PreviewStatus.READY else 0,
        'total_pages': file.preview_total_pages if status == PreviewStatus.READY else 0,
    }


def touch(file: TransferFile) -> None:
    """Record that a page was shown, at most once per `LAST_USED_RESOLUTION` per file (a cheap
    conditional write; a no-op for a file that isn't `ready`)."""
    now = timezone.now()
    TransferFile.objects.filter(pk=file.pk, preview_status=PreviewStatus.READY).filter(
        Q(preview_last_used_at__isnull=True)
        | Q(preview_last_used_at__lte=now - LAST_USED_RESOLUTION)
    ).update(preview_last_used_at=now)


# -- Building ------------------------------------------------------------------------------------


@task
def build_preview(file_id: str) -> None:
    """Build the downsized preview of one file. Only works for a file that is `building` (the
    request that claimed it queued this): a redelivered task, or one for a file whose state has
    moved on, does nothing."""
    file = TransferFile.objects.select_related('transfer').filter(pk=file_id).first()
    if file is None or file.preview_status != PreviewStatus.BUILDING:
        return
    run_build(file)


def _set_status(file: TransferFile, status: str, **fields: object) -> int:
    """Move a `building` file to `status` -- conditional, so a state that changed meanwhile (the
    transfer ended and was reset, the file removed) is never overwritten."""
    return TransferFile.objects.filter(pk=file.pk, preview_status=PreviewStatus.BUILDING).update(
        preview_status=status, **fields
    )


def run_build(file: TransferFile, *, storage: S3Storage | None = None) -> None:
    storage = storage or get_storage()
    transfer = cast(Transfer, file.transfer)
    kind = preview.kind_of(file.preview_type)
    if not file.uploaded or transfer.is_ended or transfer.files_deleted_at:
        # Ended while this was queued: its files are gone or going; nothing to preview.
        _set_status(file, PreviewStatus.NONE)
        return
    if kind not in _KIND_BY_PREVIEW_KIND:
        _set_status(file, PreviewStatus.UNAVAILABLE)
        return
    if file.size > MAX_SOURCE_BYTES:
        _set_status(file, PreviewStatus.UNAVAILABLE)
        return

    written: list[str] = []
    try:
        try:
            data = storage.get_object_bytes(file.storage_key, max_bytes=MAX_SOURCE_BYTES)
        except ObjectTooLargeError:
            _set_status(file, PreviewStatus.UNAVAILABLE)
            return
        pages, total = render_pages(data, _KIND_BY_PREVIEW_KIND[kind])
        del data
        for number, page in enumerate(pages, start=1):
            key = preview_key(transfer.pk, file.pk, number)
            written.append(key)
            storage.put_object(key, page, content_type=CONTENT_TYPE)
    except RenditionUnavailable:
        _set_status(file, PreviewStatus.UNAVAILABLE)
        _delete_written(storage, written)
        return
    except Exception as exc:
        # `RenditionFailed`, an S3 error, anything unforeseen: nothing says the file is at fault.
        if isinstance(exc, RenditionFailed):
            logger.warning('Could not build the preview of file %s: %s', file.pk, exc)
        else:
            logger.exception('Could not build the preview of file %s', file.pk)
        _set_status(file, PreviewStatus.FAILED)
        _delete_written(storage, written)
        return

    now = timezone.now()
    updated = _set_status(
        file,
        PreviewStatus.READY,
        preview_pages=len(pages),
        preview_total_pages=total,
        preview_last_used_at=now,
    )
    if not updated:
        # The file left `building` while this ran: removed, or reset by the end of its transfer, or
        # another build already finished it. Only in the first two cases is what was written an
        # orphan (a finished sibling build wrote the very same keys).
        current = (
            TransferFile.objects.filter(pk=file.pk).values_list('preview_status', flat=True).first()
        )
        if current != PreviewStatus.READY:
            _delete_written(storage, written)
        return
    fresh = Transfer.objects.filter(pk=file.transfer_id).first()
    if fresh is None or fresh.is_ended or fresh.files_deleted_at:
        # The transfer ended while this built: the end-of-life prefix sweep may already have run,
        # so nothing else will ever delete these pages (the zip builder's reasoning).
        _delete_written(storage, written)
        TransferFile.objects.filter(pk=file.pk, preview_status=PreviewStatus.READY).update(
            preview_status=PreviewStatus.NONE, preview_pages=0, preview_total_pages=0
        )


def _delete_written(storage: S3Storage, keys: list[str]) -> None:
    if not keys:
        return
    try:
        storage.delete_objects(keys)
    except Exception:
        logger.exception('Could not delete %d preview page(s)', len(keys))


# -- Reducing (the child process) ----------------------------------------------------------------

#: Exit statuses that mean "over the budget", but only from a child that had started (see
#: `rendition_render.STARTED_MARKER`): 127 is glibc's loader giving up when the address space is
#: used up.
_BUDGET_EXIT_STATUSES = frozenset({127})
#: How the OS ends a child over its budget: a failed allocation in native code (SIGABRT), a crash,
#: the CPU limit. Not SIGKILL: that is the OOM killer, memory pressure that a retry may survive.
_BUDGET_SIGNALS = frozenset(
    {
        signal.SIGABRT,
        signal.SIGSEGV,
        signal.SIGBUS,
        signal.SIGXCPU,
        signal.SIGILL,
        signal.SIGTRAP,
    }
)
_CHILD_ENV_KEYS = ('PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'SYSTEMROOT')
_warned_missing_user = False


def render_pages(data: bytes, kind: str) -> tuple[list[bytes], int]:
    """The WebP pages of `data` (an image or a PDF by `kind`) and the real page count, under the
    resource budget -- in a child process unless `FILE_TRANSFER_PREVIEW_SANDBOX` is off (pytest),
    where it runs in-process with the same error mapping. Raises `RenditionUnavailable` or
    `RenditionFailed`."""
    if not getattr(settings, 'FILE_TRANSFER_PREVIEW_SANDBOX', True):
        return _render_in_process(data, kind)
    return render_in_child(
        data,
        kind,
        memory_mb=getattr(settings, 'FILE_TRANSFER_PREVIEW_MEMORY_MB', 1024),
        cpu_seconds=getattr(settings, 'FILE_TRANSFER_PREVIEW_CPU_SECONDS', 25),
        timeout=getattr(settings, 'FILE_TRANSFER_PREVIEW_TIMEOUT_SECONDS', 60),
        child_user=getattr(settings, 'FILE_TRANSFER_PREVIEW_CHILD_USER', 'nobody'),
    )


def _render_in_process(data: bytes, kind: str) -> tuple[list[bytes], int]:
    try:
        return rendition_render.render(kind, data)
    except rendition_render.RenderError as exc:
        raise RenditionUnavailable(str(exc)) from exc
    except MemoryError as exc:
        raise RenditionUnavailable('The file needs more memory than allowed.') from exc
    except ImportError as exc:  # a library that can't be loaded is the installation's fault
        raise RenditionFailed(f'A library could not be loaded: {exc}') from exc


def _unprivileged_ids(user: str) -> dict:
    """`subprocess` arguments that run the child as `user` with no supplementary groups, when this
    process is root and `user` exists; `{}` (the child keeps our identity) otherwise."""
    global _warned_missing_user
    if not user or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return {}
    import pwd

    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        if not _warned_missing_user:
            _warned_missing_user = True
            logger.warning(
                'FILE_TRANSFER_PREVIEW_CHILD_USER %r does not exist; the preview child runs as root',
                user,
            )
        return {}
    return {'user': entry.pw_uid, 'group': entry.pw_gid, 'extra_groups': []}


def _describe_exit(returncode: int) -> str:
    if returncode < 0:
        try:
            return f'signal {signal.Signals(-returncode).name}'
        except ValueError:
            return f'signal {-returncode}'
    return f'exit status {returncode}'


def _parse_reply(stdout: bytes) -> dict | None:
    """The child's answer if it is well formed, else `None` (the reply is untrusted)."""
    try:
        reply = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(reply, dict):
        return None
    ok = reply.get('ok')
    if ok is None and reply.get('error') in ('unreadable', 'too_complex', 'failed'):
        return reply
    if (
        isinstance(ok, dict)
        and isinstance(ok.get('pages'), list)
        and all(isinstance(page, str) for page in ok['pages'])
        and isinstance(ok.get('total_pages'), int)
    ):
        return reply
    return None


def render_in_child(
    data: bytes,
    kind: str,
    *,
    memory_mb: int,
    cpu_seconds: float,
    timeout: float,
    child_user: str = '',
    command: list[str] | None = None,
) -> tuple[list[bytes], int]:
    """Run `rendition_render` in a child process under the budget (see the module docstring).
    `command` replaces the child's command line (tests only)."""
    request = {
        'kind': kind,
        'max_pages': MAX_PREVIEW_PAGES,
        'memory_mb': memory_mb,
        'cpu_seconds': cpu_seconds,
    }
    command = command or [sys.executable, '-I', str(Path(rendition_render.__file__).resolve())]
    # Only what the interpreter and its libraries need: no keys or database credentials in a
    # process that decodes files strangers sent.
    env = {key: os.environ[key] for key in _CHILD_ENV_KEYS if key in os.environ}
    payload = json.dumps(request).encode() + b'\n' + data

    def run(identity: dict) -> subprocess.CompletedProcess:
        # Its stderr is dropped (it is unbounded and can echo the file); the child's own session
        # keeps a terminal's signals from reaching it, `RLIMIT_CPU` ends one whose worker is gone.
        return subprocess.run(
            command,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=env,
            check=False,
            start_new_session=True,
            **identity,
        )

    try:
        identity = _unprivileged_ids(child_user)
        try:
            result = run(identity)
        except PermissionError:
            if not identity:
                raise
            # Not allowed to switch user (a container without CAP_SETUID): better a child with the
            # worker's own identity than no preview at all.
            logger.warning(
                "Could not start the preview child as %r; running it as the worker's user",
                child_user,
            )
            result = run({})
    except subprocess.TimeoutExpired as exc:
        logger.error('Reducing a %s took more than %s s; child killed', kind, timeout)
        raise RenditionFailed('Reducing the file took too long.') from exc
    except OSError as exc:  # could not even start it
        logger.error('Could not start the preview child: %s', type(exc).__name__)
        raise RenditionFailed('Reducing the file could not be started.') from exc

    stdout = result.stdout
    started = stdout.startswith(STARTED_MARKER)
    if started:
        stdout = stdout[len(STARTED_MARKER) :]
    reply = _parse_reply(stdout) if result.returncode == 0 else None
    if reply is None:
        description = _describe_exit(result.returncode)
        if (result.returncode < 0 and -result.returncode in _BUDGET_SIGNALS) or (
            started and result.returncode in _BUDGET_EXIT_STATUSES
        ):
            logger.warning(
                'The child reducing a %s (%d bytes) ended with %s: over its budget (%d MB, %s CPU s)',
                kind,
                len(data),
                description,
                memory_mb,
                cpu_seconds,
            )
            raise RenditionUnavailable('The file needs more memory or time than is allowed.')
        logger.error(
            'The child reducing a %s (%d bytes) ended with %s and no usable answer',
            kind,
            len(data),
            description,
        )
        raise RenditionFailed('Reducing the file failed.')

    ok = reply.get('ok')
    if ok is not None:
        try:
            pages = [base64.b64decode(page, validate=True) for page in ok['pages']]
        except ValueError as exc:  # invalid base64 (`binascii.Error` is one)
            logger.error('The child reducing a %s sent undecodable data', kind)
            raise RenditionFailed('Reducing the file failed.') from exc
        if not pages or len(pages) > MAX_PREVIEW_PAGES:
            raise RenditionFailed('Reducing the file failed.')
        return pages, ok['total_pages']
    if reply['error'] == 'failed':
        logger.error('The preview child reported a failure: %s', str(reply.get('message'))[:200])
        raise RenditionFailed('Reducing the file failed.')
    raise RenditionUnavailable(str(reply.get('message', 'The file could not be read.')))


# -- Deleting ------------------------------------------------------------------------------------


def delete_renditions(file: TransferFile, *, storage: S3Storage | None = None) -> None:
    """Delete one file's pages and forget them (the file's own removal, a purge). Best effort for
    the objects: a failure is logged, since whatever is left lies under the transfer's prefix and
    goes with it when the transfer ends. Cheap when the file never had any (no S3 call)."""
    if file.preview_status not in (PreviewStatus.READY, PreviewStatus.BUILDING):
        return
    try:
        (storage or get_storage()).delete_prefix(preview_prefix(file.transfer_id, file.pk))
    except Exception:
        logger.exception('Could not delete the preview pages of file %s', file.pk)


def purge_unused(
    now: datetime | None = None, *, storage: S3Storage | None = None, batch: int = 200
) -> int:
    """The daily job: delete the pages of every file whose preview was last used (or, never used,
    was built) more than `PREVIEW_CACHE_DAYS` ago, and reset it to `none` so the next click builds
    it again. The state is claimed first (a conditional write on the same condition), the objects
    deleted after: a click in between finds a file without a preview, never pages that vanish. A
    failing file is logged and skipped. Returns how many were reset."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=PREVIEW_CACHE_DAYS)
    stale = Q(preview_status=PreviewStatus.READY) & (
        Q(preview_last_used_at__lt=cutoff)
        | Q(preview_last_used_at__isnull=True, preview_started_at__lt=cutoff)
        | Q(preview_last_used_at__isnull=True, preview_started_at__isnull=True)
    )
    storage = storage or get_storage()
    count = 0
    last_pk = None
    while True:
        queryset = TransferFile.objects.filter(stale).order_by('pk')
        if last_pk is not None:
            queryset = queryset.filter(pk__gt=last_pk)
        files = list(queryset[:batch])
        if not files:
            return count
        for file in files:
            last_pk = file.pk
            try:
                claimed = TransferFile.objects.filter(stale, pk=file.pk).update(
                    preview_status=PreviewStatus.NONE, preview_pages=0, preview_total_pages=0
                )
                if claimed:
                    storage.delete_prefix(preview_prefix(file.transfer_id, file.pk))
                    count += 1
            except Exception:
                logger.exception('Could not purge the preview of file %s', file.pk)
