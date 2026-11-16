"""Public download page logic (spec sections 3-4): availability, password gating, and per-file
(or, for "download all", per-zip) download counting.
"""

import time
from collections.abc import MutableMapping

from django.contrib.sessions.backends.base import SessionBase
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.services.storage import INLINE_CONTENT_TYPES

from ..models import DownloadEvent, Transfer, TransferFile, TransferStatus, ZipStatus
from .emails import send_download_notification
from .lifecycle import end_transfer
from .password import verify_password
from .storage import GET_URL_EXPIRES_SECONDS, S3Storage, get_storage

#: A visitor's session (or a plain mapping, in tests).
SessionStore = SessionBase | MutableMapping

#: Session key of the files "View full" has already counted as a download (see `mark_counted`).
COUNTED_SESSION_KEY = 'ft_counted_downloads'
#: How long a counted file stays free to download once more: the lifetime of the presigned URL
#: the count handed out, which is also how long the deferred deletion of a transfer that the
#: count ended keeps its files.
COUNTED_WINDOW_SECONDS = GET_URL_EXPIRES_SECONDS
#: How many such records a session keeps (the oldest are dropped).
_MAX_COUNTED = 50


def is_available(transfer: Transfer) -> bool:
    """Whether the transfer's public page should work at all. The page itself must never say
    *why* it doesn't (spec section 3): expired, disabled, suspended, deleted, taken down,
    on hold pending abuse review, and limit-reached all render the same neutral message."""
    if transfer.held_for_review_at:
        return False
    if transfer.status != TransferStatus.ACTIVE:
        return False
    if transfer.expires_at and transfer.expires_at <= timezone.now():
        return False
    remaining = transfer.downloads_remaining
    if remaining is not None and remaining <= 0:
        return False
    return True


def requires_password(transfer: Transfer) -> bool:
    return bool(transfer.password_hash)


def check_password(transfer: Transfer, raw_password: str) -> bool:
    if not transfer.password_hash:
        return True
    return verify_password(raw_password, transfer.password_hash)


def _counted_key(transfer_id: object, file_id: object) -> str:
    return f'{transfer_id}:{file_id}'


def _live_counted(session: SessionStore) -> dict[str, float]:
    """The session's counted-download records that have not run out (never raises: a damaged
    value is as good as none)."""
    raw = session.get(COUNTED_SESSION_KEY)
    if not isinstance(raw, dict):
        return {}
    now = time.time()
    return {
        key: until for key, until in raw.items() if isinstance(until, int | float) and until > now
    }


def mark_counted(session: SessionStore, transfer_id: object, file_id: object) -> None:
    """Remember in this session that "View full" counted this file as a download, so the Download
    that follows it (the browser may not have kept the bytes) serves the file **without counting a
    second time**, for `COUNTED_WINDOW_SECONDS`. Like the unlock flags, it lives in the visitor's
    own session: nothing here can be set by a request that doesn't also pass the download gates."""
    live = _live_counted(session)
    live[_counted_key(transfer_id, file_id)] = time.time() + COUNTED_WINDOW_SECONDS
    if len(live) > _MAX_COUNTED:
        for key in sorted(live, key=lambda k: live[k])[: len(live) - _MAX_COUNTED]:
            del live[key]
    session[COUNTED_SESSION_KEY] = live


def is_counted(session: SessionStore, transfer_id: object, file_id: object) -> bool:
    return _counted_key(transfer_id, file_id) in _live_counted(session)


def consume_counted(session: SessionStore, transfer_id: object, file_id: object) -> bool:
    """Whether this file was counted by "View full" in this session -- and forget it: the
    Download that follows is the one free repeat, the next after it counts again."""
    live = _live_counted(session)
    found = live.pop(_counted_key(transfer_id, file_id), None) is not None
    if found:
        session[COUNTED_SESSION_KEY] = live
    return found


def is_replayable(session: SessionStore, transfer: Transfer, file_id: object) -> bool:
    """Whether a Download of this file may still be served although the transfer is no longer
    `is_available`: only when this session's "View full" counted it, and only because that count
    may itself have ended the transfer (the last allowed download keeps the files for the grace
    window, `delete_files=False`). So: counted in this session, files not deleted yet, not on hold,
    and still `ACTIVE` or `EXPIRED` -- never disabled, suspended, deleted or taken down, which the
    sender or staff did on purpose."""
    return (
        is_counted(session, transfer.id, file_id)
        and not transfer.files_deleted_at
        and not transfer.held_for_review_at
        and transfer.status in (TransferStatus.ACTIVE, TransferStatus.EXPIRED)
    )


def download_url(
    file: TransferFile, *, inline: bool = False, storage: S3Storage | None = None
) -> str:
    """A short-lived presigned GET URL of the file's own bytes. `inline` is for showing it (the
    dialog's "View full"): honoured only for a file whose sniffed type may be served inline
    (`INLINE_CONTENT_TYPES`), with that type as the `Content-Type`; any other file is an
    attachment."""
    storage = storage or get_storage()
    if inline and file.preview_type in INLINE_CONTENT_TYPES:
        return storage.presigned_get_url(
            file.storage_key, file.name, disposition='inline', content_type=file.preview_type
        )
    return storage.presigned_get_url(file.storage_key, file.name)


def record_download(
    transfer: Transfer,
    file: TransferFile | None,
    ip: str | None,
    *,
    storage: S3Storage | None = None,
    inline: bool = False,
) -> str:
    """Record a download of `file` and return a short-lived presigned URL for it (`inline`: one
    that shows the file, see `download_url`, for "View full" -- which counts exactly as this).

    `file=None` means "download all" (spec section 5): the zip built at `transfer.zip_key`,
    which must already be `ZipStatus.READY`. Either way this counts as exactly one download
    against `max_downloads` (spec section 4) -- the zip is otherwise handled identically to a
    regular file download, including the password gate (checked by the view before this is ever
    called) and the sender's "file downloaded" notification.

    Locks the transfer row for the duration of the check-count-record sequence, so N concurrent
    requests against a transfer with one download remaining can't all slip through before any of
    them is counted. The presigned URL itself is only built after the transaction commits.

    Raises:
        ValidationError: the transfer isn't available, `file` doesn't belong to it, or (for the
            zip) the zip isn't ready.
    """
    with transaction.atomic():
        locked_transfer = Transfer.objects.select_for_update().get(pk=transfer.pk)
        if not is_available(locked_transfer):
            raise ValidationError('This transfer is no longer available.')
        if file is not None and file.transfer_id != locked_transfer.id:
            raise ValidationError('File does not belong to this transfer.')
        if file is None and locked_transfer.zip_status != ZipStatus.READY:
            raise ValidationError('The zip is not ready yet.')

        DownloadEvent.objects.create(transfer=locked_transfer, file=file, ip=ip)
        transaction.on_commit(
            lambda: send_download_notification.enqueue(
                str(locked_transfer.id), str(file.id) if file else None
            )
        )

        # End the transfer the moment the limit is *reached*, rather than waiting for the next
        # `expire_transfers` tick, so the same link can't be reused past its limit in the
        # meantime. Deletion of the files themselves is deferred (`delete_files=False`): the
        # presigned URL this call is about to hand back must still resolve, so
        # `expire_transfers` removes the objects once it's had time to expire instead. That job
        # also re-checks this ending as a fallback in case the process dies right here.
        remaining = locked_transfer.downloads_remaining
        if remaining is not None and remaining <= 0:
            end_transfer(locked_transfer, TransferStatus.EXPIRED, delete_files=False)

    storage = storage or get_storage()
    if file is None:
        return storage.presigned_get_url(locked_transfer.zip_key, 'all.zip')
    return download_url(file, inline=inline, storage=storage)
