"""Draft transfer and upload management: creating a draft, adding/removing files, issuing
presigned multipart-upload URLs, and completing a file's upload once the browser has PUT every
part directly to S3 (spec section 2). Shared by the send-page HTMX/JSON views and, for cleanup,
by the `cleanup_drafts` job.
"""

import math

from botocore.exceptions import ClientError
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.utils import timezone

from ..models import FileTransferSettings, Transfer, TransferFile, TransferStatus
from . import anon_limits, limits
from .storage import PART_SIZE_BYTES, S3Storage, get_storage, storage_key, transfer_prefix


def create_draft(owner: AbstractBaseUser) -> Transfer:
    """Start a new transfer: a draft with no files yet."""
    return Transfer.objects.create(owner=owner, status=TransferStatus.DRAFT)


def get_or_create_draft(owner: AbstractBaseUser) -> Transfer:
    """Reuse the owner's most recent still-empty draft rather than creating a new one on every
    visit to the send page: `send_page` used to call `create_draft` unconditionally, so simply
    reloading it (or leaving the tab open) left an "Untitled transfer" row on the dashboard for up
    to the 24h `cleanup_drafts` grace period. A draft that already has files is left alone and a
    fresh one is created instead -- the send page's file list is only tracked in the browser, so
    reusing one that already has files would silently attach new uploads to files the page has no
    record of.
    """
    existing = (
        Transfer.objects.filter(owner=owner, status=TransferStatus.DRAFT)
        .annotate(_file_count=Count('files'))
        .filter(_file_count=0)
        .order_by('-created_at')
        .first()
    )
    return existing or create_draft(owner)


def add_file(
    transfer: Transfer,
    name: str,
    size: int,
    *,
    ip: str | None = None,
    cookie_id: str = '',
    client_last_modified: int | None = None,
    storage: S3Storage | None = None,
) -> TransferFile:
    """Register a new file on a draft transfer and start its multipart upload.

    Returns the `TransferFile`; the caller still needs `presign_parts` to get upload URLs.

    `ip`/`cookie_id` only matter for an anonymous transfer (`transfer.owner_id is None`): they're
    checked against the per-IP-per-day byte cap (spec section 13) so a sender can't blow past it
    by uploading without ever confirming -- the caller (`views.anonymous`) always passes them for
    an anonymous draft; a logged-in upload has no caller-supplied IP/cookie to check against and
    doesn't need one, since logged-in senders are billed, not capped.

    `client_last_modified` is the browser `File` object's `lastModified` (ms since epoch), when the
    caller has one -- stored so a later `list_uploaded_parts` caller (the send page's upload JS,
    after a reload) can match a re-selected file back to this row by name + size + `lastModified`
    rather than name + size alone (spec: resumable uploads).

    Raises:
        ValidationError: the transfer isn't a draft, or the file would break a tier limit (spec
            section 1: max file size, max files, max total size -- logged-in or anonymous,
            whichever `transfer.owner_id` selects) or the anonymous per-IP-per-day byte cap.
    """
    if transfer.status != TransferStatus.DRAFT:
        raise ValidationError('Files can only be added to a draft transfer.')

    name = limits.validate_filename(name)
    settings_row = FileTransferSettings.load()
    if transfer.owner_id is None:
        limits.validate_new_file_anonymous(transfer, size, settings_row)
        anon_limits.check_upload_bytes_cap(transfer, size, ip, cookie_id, settings_row)
    else:
        limits.validate_new_file(transfer, size, settings_row)

    storage = storage or get_storage()
    file = TransferFile(
        transfer=transfer,
        name=name,
        size=size,
        client_last_modified=client_last_modified,
        # Pinned at add-file time rather than read live off `PART_SIZE_BYTES` wherever a part
        # count/size is needed later (`presign_parts`, `resume_file`'s response) -- see the
        # field's own docstring for why that matters for an upload that spans a config change.
        part_size_bytes=PART_SIZE_BYTES,
    )
    # Keyed by file id only (not the filename): see `storage_key`'s docstring for why.
    key = storage_key(transfer.id, file.id)
    file.storage_key = key
    file.upload_id = storage.create_multipart_upload(key)
    file.save()
    return file


def _max_part_number(file_size: int, part_size_bytes: int) -> int:
    return max(1, math.ceil(file_size / part_size_bytes))


def _expected_part_content_length(
    file_size: int, part_number: int, part_count: int, part_size_bytes: int
) -> int:
    """Every part is `part_size_bytes` except the last, which is whatever's left over."""
    if part_number < part_count:
        return part_size_bytes
    return file_size - part_size_bytes * (part_count - 1)


def part_count_for(file: TransferFile) -> int:
    """How many parts `file`'s upload has, at the part size it was started with -- used by every
    caller (add-file/part-urls/resume responses) that needs to tell a client the shape of the
    upload without duplicating this arithmetic."""
    return _max_part_number(file.size, file.part_size_bytes)


def presign_parts(
    file: TransferFile, parts: list[dict], *, storage: S3Storage | None = None
) -> dict[int, str]:
    """Presigned PUT URLs for the given parts of `file`'s multipart upload.

    `parts` is `[{'part_number': n, 'checksum_sha256': <base64 SHA-256 of that part>}, ...]`: the
    browser computes each part's checksum with `crypto.subtle` *before* asking for its URL, and
    that checksum is baked into the presigned URL's signature (`ChecksumSHA256`, spec section 11)
    -- S3 rejects the `UploadPart` call outright if the header on the PUT doesn't match the value
    the URL was signed for, which is what actually verifies the bytes made it over intact (a
    presigned URL can't itself carry a value the *server* doesn't already know, so this only works
    because the checksum is computed client-side first and handed to us, not the other way
    round). The content length expected for each part number is signed the same way, so a part
    can't be padded past its declared size either.

    Raises:
        ValidationError: the upload isn't in progress, a part number is outside the valid
            `1..ceil(size / file.part_size_bytes)` range for this file's declared size (which would
            otherwise let a sender request far more parts -- and so upload far more bytes -- than
            the size it declared), or a checksum isn't a well-formed base64-encoded SHA-256.
    """
    if not file.upload_id:
        raise ValidationError('File has no upload in progress.')

    part_count = part_count_for(file)
    storage = storage or get_storage()
    urls: dict[int, str] = {}
    for part in parts:
        part_number = part['part_number']
        if part_number < 1 or part_number > part_count:
            raise ValidationError(f'Invalid part number {part_number} for this file.')
        checksum = limits.validate_checksum_sha256(part['checksum_sha256'])
        content_length = _expected_part_content_length(
            file.size, part_number, part_count, file.part_size_bytes
        )
        urls[part_number] = storage.presign_part_url(
            file.storage_key,
            file.upload_id,
            part_number,
            content_length=content_length,
            checksum_sha256=checksum,
        )
    return urls


def complete_file_upload(
    file: TransferFile, parts: list[dict], *, storage: S3Storage | None = None
) -> TransferFile:
    """Complete `file`'s multipart upload and verify it landed in S3 with the expected size.

    `parts` is `[{'PartNumber': n, 'ETag': etag, 'ChecksumSHA256': checksum}, ...]`, one entry per
    part the browser PUT, in order; passed straight through to S3's `CompleteMultipartUpload`.

    Raises:
        ValidationError: S3 rejected completing the upload (e.g. no parts were ever PUT), or the
            object's size in S3 doesn't match what was declared up front -- in which case the
            object S3 just assembled is deleted immediately rather than left as an orphan (the
            completed upload is no longer "in progress", so neither `remove_file` nor
            `abort_draft` would otherwise ever clean it up).
    """
    if not file.upload_id:
        raise ValidationError('File has no upload in progress.')

    storage = storage or get_storage()
    try:
        storage.complete_multipart_upload(file.storage_key, file.upload_id, parts)
        info = storage.head_object(file.storage_key)
    except ClientError as exc:
        raise ValidationError(f'Could not complete the upload: {exc}') from exc

    if info.size != file.size:
        storage.delete_object(file.storage_key)
        file.upload_id = ''
        file.save(update_fields=['upload_id'])
        raise ValidationError(
            f'Uploaded file size ({info.size}) does not match the declared size ({file.size}).'
        )

    file.checksum = info.checksum_sha256
    file.uploaded = True
    file.upload_id = ''
    file.save(update_fields=['checksum', 'uploaded', 'upload_id'])
    return file


class UploadExpired(Exception):
    """Raised by `list_uploaded_parts` when S3 no longer recognizes `file`'s upload id (the
    bucket's lifecycle rule aborted an incomplete multipart upload after a day, or a previous run
    of `cleanup_drafts` beat this request to it). The caller should call `restart_upload` and have
    the browser re-upload the file from scratch under the fresh upload id it returns.
    """


def list_uploaded_parts(file: TransferFile, *, storage: S3Storage | None = None) -> list[dict]:
    """List the parts of `file`'s in-progress multipart upload already sitting in S3 (spec:
    resumable uploads) -- `[{'PartNumber': n, 'ETag': etag, 'Size': size, 'ChecksumSHA256': sum},
    ...]` -- so the browser can skip re-uploading (and re-hashing) the ones it already sent in an
    earlier page load and only PUT what's missing. The checksum must be carried back into the part
    list the caller eventually completes the upload with (see `S3Storage.list_parts`'s docstring).

    Raises:
        ValidationError: the file has no upload in progress (already completed, or never
            started), or S3 refused the request for a reason other than the upload having expired
            (e.g. `AccessDenied`) -- surfaced as a plain validation error rather than an unhandled
            500, same as every other S3 failure this service layer can turn into one.
        UploadExpired: S3 no longer knows about the upload id; see `restart_upload`.
    """
    if not file.upload_id:
        raise ValidationError('File has no upload in progress.')

    storage = storage or get_storage()
    try:
        return storage.list_parts(file.storage_key, file.upload_id)
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', '')
        if code == 'NoSuchUpload':
            raise UploadExpired() from exc
        raise ValidationError(f"Could not list the upload's parts: {exc}") from exc


def restart_upload(file: TransferFile, *, storage: S3Storage | None = None) -> TransferFile:
    """Abandon `file`'s expired/aborted multipart upload and start a fresh one at the same storage
    key, so the browser can resume uploading every part from scratch under a new upload id (spec:
    resumable uploads, "handle an expired/aborted multipart upload gracefully"). `file.size` and
    `file.name` are unchanged -- only the S3-side upload identity resets.

    The DB update is conditional on `file` still holding the exact `upload_id`/`uploaded=False`
    state it was read in (a compare-and-swap, not a blind `save()`): two tabs open on the same
    draft can both call this for the same file at nearly the same time -- e.g. tab A's `.../parts/`
    PUTs finish and it completes the upload (`upload_id=''`, `uploaded=True`) right as tab B's
    stale `list_uploaded_parts` call (still holding the old upload id) gets `NoSuchUpload` and
    calls this. Without the guard, tab B's write would land after tab A's and silently flip the
    now-completed file back to an empty, `uploaded=False` upload, orphaning the object tab A just
    finished uploading (spec: resumable uploads, issue #55 phase 3 review)."""
    storage = storage or get_storage()
    old_upload_id = file.upload_id
    if old_upload_id:
        # Already gone as far as S3 is concerned (that's the whole reason this is being called),
        # but harmless to ask again -- same reasoning as `abort_multipart_upload`'s own docstring.
        storage.abort_multipart_upload(file.storage_key, old_upload_id)
    new_upload_id = storage.create_multipart_upload(file.storage_key)

    updated = TransferFile.objects.filter(
        pk=file.pk, upload_id=old_upload_id, uploaded=False
    ).update(upload_id=new_upload_id, uploaded=False)
    if not updated:
        # Lost the race: something else (a completion, a remove, another restart) already changed
        # this file's upload since `file` was read. The just-created upload is never going to be
        # used, so abandon it and hand back the file's current, authoritative state instead of
        # overwriting whatever that something else just did.
        storage.abort_multipart_upload(file.storage_key, new_upload_id)
        file.refresh_from_db()
        return file

    file.upload_id = new_upload_id
    file.uploaded = False
    return file


def remove_file(file: TransferFile, *, storage: S3Storage | None = None) -> None:
    """Remove a file from a draft transfer: abort its multipart upload (if still in progress),
    delete its object from S3 (if the upload had completed), then drop the row."""
    storage = storage or get_storage()
    if file.upload_id:
        storage.abort_multipart_upload(file.storage_key, file.upload_id)
    if file.uploaded:
        storage.delete_object(file.storage_key)
    file.delete()


def abort_draft(transfer: Transfer, *, storage: S3Storage | None = None) -> None:
    """Abandon an entire draft (or unconfirmed) transfer: abort every file's in-progress
    multipart upload, sweep every object under the transfer's S3 prefix (rather than trusting
    each file's `uploaded` flag, which is one more place a bug could leave an orphan), then
    soft-delete the transfer. Used by `cleanup_drafts`."""
    storage = storage or get_storage()
    for file in transfer.files.all():
        if file.upload_id:
            storage.abort_multipart_upload(file.storage_key, file.upload_id)
    storage.delete_prefix(transfer_prefix(transfer.id))
    transfer.files.all().delete()
    transfer.status = TransferStatus.DELETED
    transfer.deleted_at = timezone.now()
    transfer.save(update_fields=['status', 'deleted_at'])
