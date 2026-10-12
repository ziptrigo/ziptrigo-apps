"""Direct-to-S3 uploads: the browser (or the API/CLI) never sends the file through Django.

1. `start_upload` validates the request and creates a `Receipt` with `uploaded=False`, returning a
   presigned PUT URL whose signature binds the exact `Content-Length` and the client-computed
   `x-amz-checksum-sha256`: S3 refuses a body that differs in size or content. (That needs a SigV4
   client, which `services.storage.get_storage` builds; the core storage fails closed otherwise.)
2. The client PUTs the file to the URL.
3. `complete_upload` checks the object landed with the declared size, reads it back, **sniffs its
   magic bytes** (the claimed type is never trusted: a mismatch deletes the object and fails the
   receipt as `unsupported_type`), computes the SHA-256 server-side, counts the bytes against the
   account's storage and queues processing.

A receipt that is never completed is cleaned up after 24 hours (`abandon_upload`, by the
`cleanup_uploads` job). Nothing is billed for an upload until it completes.
"""

import base64
import binascii
import logging
import os
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import NoReturn

from botocore.exceptions import BotoCoreError, ClientError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services.storage import ObjectTooLargeError

from ..models import FailureReason, Receipt, ReceiptSource, ReceiptsSettings, ReceiptStatus
from . import documents
from .accounts import add_stored_bytes, get_account
from .errors import AccountFrozenError, DailyLimitExceededError
from .processing import enqueue_processing
from .storage import PUT_URL_EXPIRES_SECONDS, file_key, get_storage

logger = logging.getLogger(__name__)

#: How long an upload may stay un-completed before `cleanup_uploads` removes it.
ABANDONED_AFTER = timedelta(hours=24)

_EXTENSION_TYPES = {
    '.pdf': documents.PDF,
    '.jpg': documents.JPEG,
    '.jpeg': documents.JPEG,
    '.png': documents.PNG,
    '.webp': documents.WEBP,
    '.heic': documents.HEIC,
    '.heif': documents.HEIC,
}
_TYPE_ALIASES = {
    'image/jpg': documents.JPEG,
    'image/pjpeg': documents.JPEG,
    'image/x-png': documents.PNG,
    'image/heif': documents.HEIC,
    'image/heic-sequence': documents.HEIC,
    'image/heif-sequence': documents.HEIC,
}
_GENERIC_TYPES = ('', 'application/octet-stream', 'binary/octet-stream')


@dataclass(slots=True)
class UploadTicket:
    """What a client needs to upload one file: PUT the bytes to `url` with these `headers`."""

    receipt: Receipt
    url: str
    headers: dict[str, str]
    expires_in: int


def clean_filename(name: str) -> str:
    """A display-safe name: no control characters, no directory part, at most 255 characters."""
    stripped = ''.join(ch for ch in name if unicodedata.category(ch) != 'Cc')
    base = stripped.replace('\\', '/').rsplit('/', 1)[-1].strip()
    return base[:255] or 'receipt'


def declared_content_type(filename: str, content_type: str) -> str:
    """The content type the client claims, normalized (`image/jpg` -> `image/jpeg`, `image/heif`
    -> `image/heic`), falling back to the file extension when the browser sent none -- Chrome
    sends an empty type for `.heic`."""
    declared = (content_type or '').split(';')[0].strip().lower()
    declared = _TYPE_ALIASES.get(declared, declared)
    if declared in _GENERIC_TYPES:
        declared = _EXTENSION_TYPES.get(os.path.splitext(filename)[1].lower(), declared)
    return declared


def _validate_checksum(checksum_sha256: str) -> str:
    """The base64 SHA-256 S3 expects in `x-amz-checksum-sha256` (44 characters)."""
    try:
        if len(base64.b64decode(checksum_sha256, validate=True)) == 32:
            return checksum_sha256
    except binascii.Error, ValueError:
        pass
    raise ValidationError('The file checksum must be a base64-encoded SHA-256.')


def start_upload(
    user: User,
    filename: str,
    size: int,
    content_type: str,
    checksum_sha256: str,
    *,
    source: str = ReceiptSource.UPLOAD,
) -> UploadTicket:
    """Register a new upload and presign its PUT.

    Raises:
        AccountFrozenError: the account's storage is unpaid.
        DailyLimitExceededError: `max_files_per_user_per_day` receipts were already added in the
            last 24 hours.
        ValidationError: the size is not between 1 byte and `max_file_bytes`, the type is not an
            allowed one, or the checksum is malformed.
    """
    config = ReceiptsSettings.load()
    account = get_account(user)
    if account.is_frozen:
        raise AccountFrozenError()

    filename = clean_filename(filename)
    if size < 1:
        raise ValidationError('The file is empty.')
    if size > config.max_file_bytes:
        raise ValidationError(
            f'The file is too large (the limit is {config.max_file_bytes} bytes).'
        )
    declared = declared_content_type(filename, content_type)
    if not config.is_type_allowed(declared):
        raise ValidationError('This file type is not supported. Use PDF, JPG, PNG, HEIC or WebP.')
    checksum_sha256 = _validate_checksum(checksum_sha256)

    since = timezone.now() - timedelta(hours=24)
    if Receipt.objects.filter(user=user, created_at__gte=since).count() >= (
        config.max_files_per_user_per_day
    ):
        raise DailyLimitExceededError(
            f'You can add up to {config.max_files_per_user_per_day} receipts per day.'
        )

    receipt = Receipt(
        user=user,
        source=source,
        original_filename=filename,
        content_type=declared,
        size_bytes=size,
        upload_checksum_sha256=checksum_sha256,
        uploaded=False,
        status=ReceiptStatus.RECEIVED,
    )
    receipt.file_key = file_key(user.pk, receipt.pk)
    receipt.save()
    url = get_storage().presigned_put_url(
        receipt.file_key, content_length=size, checksum_sha256=checksum_sha256
    )
    return UploadTicket(
        receipt=receipt,
        url=url,
        headers={'x-amz-checksum-sha256': checksum_sha256},
        expires_in=PUT_URL_EXPIRES_SECONDS,
    )


def _reject(receipt: Receipt, reason: str, message: str) -> NoReturn:
    """Delete the uploaded object and fail the receipt (it stays in the list so the user sees
    what happened), then tell the caller. Nothing was ever counted as stored."""
    try:
        get_storage().delete_object(receipt.file_key)
    except ClientError, BotoCoreError:
        logger.exception('Could not delete the rejected upload %s', receipt.file_key)
    Receipt.objects.filter(pk=receipt.pk, uploaded=False).update(
        uploaded=True,
        status=ReceiptStatus.FAILED,
        failure_reason=reason,
        file_key='',
        size_bytes=0,
        last_error=message[:255],
    )
    raise ValidationError(message)


def complete_upload(receipt: Receipt) -> Receipt:
    """Finish an upload after the client's PUT. Idempotent: completing a completed receipt returns
    it unchanged. The caller must have checked that `receipt` belongs to the requesting user.

    Raises:
        ValidationError: the object isn't there (yet), or it was rejected -- wrong size, too
            large, or contents that aren't an allowed type (the object is deleted and the receipt
            fails with `unsupported_type`/`too_large`).
    """
    if receipt.uploaded:
        return receipt
    config = ReceiptsSettings.load()
    storage = get_storage()

    try:
        info = storage.head_object(receipt.file_key)
    except (ClientError, BotoCoreError) as exc:
        raise ValidationError('The file has not been uploaded.') from exc
    if info.size != receipt.size_bytes:
        _reject(receipt, FailureReason.UNREADABLE, 'The uploaded file is not the size declared.')
    if (
        receipt.upload_checksum_sha256
        and info.checksum_sha256
        and info.checksum_sha256 != receipt.upload_checksum_sha256
    ):
        _reject(receipt, FailureReason.UNREADABLE, 'The uploaded file is corrupted.')

    try:
        data = storage.get_object_bytes(receipt.file_key, max_bytes=config.max_file_bytes)
    except ObjectTooLargeError:
        _reject(receipt, FailureReason.TOO_LARGE, 'The file is too large.')
    except (ClientError, BotoCoreError) as exc:
        raise ValidationError('The file could not be read back; try again.') from exc

    sniffed = documents.sniff_content_type(data)
    if sniffed is None or not config.is_type_allowed(sniffed):
        _reject(
            receipt,
            FailureReason.UNSUPPORTED_TYPE,
            'This file is not a supported type. Use PDF, JPG, PNG, HEIC or WebP.',
        )
    if receipt.content_type and sniffed != receipt.content_type:
        _reject(
            receipt,
            FailureReason.UNSUPPORTED_TYPE,
            "The file's contents do not match its type.",
        )

    with transaction.atomic():
        won = Receipt.objects.filter(pk=receipt.pk, uploaded=False).update(
            uploaded=True,
            content_type=sniffed,
            sha256=sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        if won:
            add_stored_bytes(receipt.user_id, len(data))
            enqueue_processing(receipt.pk)
    receipt.refresh_from_db()
    return receipt


def abandon_upload(receipt: Receipt) -> None:
    """Remove a never-completed upload: its object (if any landed) and its row."""
    if receipt.uploaded:
        return
    if receipt.file_key:
        get_storage().delete_object(receipt.file_key)
    Receipt.objects.filter(pk=receipt.pk, uploaded=False).delete()
