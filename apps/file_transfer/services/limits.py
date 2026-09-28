"""Logged-in sender limits (spec sections 1 and 3), read from the `FileTransferSettings`
singleton. Shared by the send-page form and the upload views, so both enforce the same rules
(see the HTMX form-view conventions in `CLAUDE.md`).
"""

import base64
import binascii
import unicodedata

from django.core.exceptions import ValidationError

from ..models import FileTransferSettings, Transfer, TransferFile

#: Message body (spec section 2 defaults): plain text, no title field.
MAX_MESSAGE_LENGTH = 2000

#: A base64-encoded SHA-256 digest is always 32 raw bytes.
_SHA256_DIGEST_SIZE = 32


def validate_message(message: str) -> None:
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValidationError(f'Message must be at most {MAX_MESSAGE_LENGTH} characters.')


def validate_filename(name: str) -> str:
    """Strip control characters (including CR/LF) from a file name before it's stored or ever
    reaches an S3 key or a `Content-Disposition` header, then require something non-empty to be
    left. Names aren't otherwise restricted -- non-ASCII is fine (`storage.presigned_get_url`
    encodes it correctly) -- since the storage key is keyed by file id, not by this name (see
    `apps.file_transfer.services.storage.storage_key`)."""
    cleaned = ''.join(ch for ch in name if unicodedata.category(ch) != 'Cc').strip()
    if not cleaned:
        raise ValidationError('File name is required.')
    return cleaned[:255]


def validate_checksum_sha256(value: str) -> str:
    """Validate that `value` is a well-formed base64-encoded SHA-256 digest (what the browser
    sends as `x-amz-checksum-sha256`, spec section 11), returning it unchanged."""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError('Invalid checksum.') from exc
    if len(raw) != _SHA256_DIGEST_SIZE:
        raise ValidationError('Invalid checksum.')
    return value


def _validate_new_file(
    transfer: Transfer,
    size: int,
    *,
    max_file_size_bytes: int,
    max_files: int,
    max_total_size_bytes: int,
) -> None:
    if size <= 0:
        raise ValidationError('File is empty.')
    if size > max_file_size_bytes:
        limit_gb = max_file_size_bytes / 1024**3
        raise ValidationError(f'File is larger than the {limit_gb:.1f} GB limit per file.')

    existing = transfer.files.all()
    if existing.count() >= max_files:
        raise ValidationError(f'A transfer can have at most {max_files} files.')

    total = sum(f.size for f in existing) + size
    if total > max_total_size_bytes:
        limit_gb = max_total_size_bytes / 1024**3
        raise ValidationError(f'Transfer would exceed the {limit_gb:.1f} GB total size limit.')


def validate_new_file(transfer: Transfer, size: int, settings: FileTransferSettings) -> None:
    """Validate that adding one more file of `size` bytes to `transfer` stays within the
    logged-in limits. Call before creating the `TransferFile` / multipart upload."""
    _validate_new_file(
        transfer,
        size,
        max_file_size_bytes=settings.logged_in_max_file_size_bytes,
        max_files=settings.logged_in_max_files,
        max_total_size_bytes=settings.logged_in_max_total_size_bytes,
    )


def validate_new_file_anonymous(
    transfer: Transfer, size: int, settings: FileTransferSettings
) -> None:
    """Same as `validate_new_file`, against the separate (smaller) anonymous-sender limits (spec
    section 1)."""
    _validate_new_file(
        transfer,
        size,
        max_file_size_bytes=settings.anonymous_max_file_size_bytes,
        max_files=settings.anonymous_max_files,
        max_total_size_bytes=settings.anonymous_max_total_size_bytes,
    )


def _validate_recipients(emails: list[str], *, max_recipients: int) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for email in emails:
        key = email.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(email.strip())

    if len(deduped) > max_recipients:
        raise ValidationError(f'A transfer can have at most {max_recipients} recipients.')
    return deduped


def validate_recipients(emails: list[str], settings: FileTransferSettings) -> list[str]:
    """Deduplicate (case-insensitively) and validate a recipient list against the logged-in
    max-recipients limit. Returns the deduplicated list, preserving first-seen order and casing."""
    return _validate_recipients(emails, max_recipients=settings.logged_in_max_recipients)


def validate_recipients_anonymous(emails: list[str], settings: FileTransferSettings) -> list[str]:
    """Same as `validate_recipients`, against the separate (smaller) anonymous-sender limit."""
    return _validate_recipients(emails, max_recipients=settings.anonymous_max_recipients)


def validate_has_files(transfer: Transfer) -> None:
    if not TransferFile.objects.filter(transfer=transfer, uploaded=True).exists():
        raise ValidationError('Add at least one file before sending.')
