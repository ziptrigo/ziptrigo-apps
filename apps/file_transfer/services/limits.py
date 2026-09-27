"""Logged-in sender limits (spec sections 1 and 3), read from the `FileTransferSettings`
singleton. Shared by the send-page form and the upload views, so both enforce the same rules
(see the HTMX form-view conventions in `CLAUDE.md`).
"""

from django.core.exceptions import ValidationError

from ..models import FileTransferSettings, Transfer, TransferFile

#: Message body (spec section 2 defaults): plain text, no title field.
MAX_MESSAGE_LENGTH = 2000


def validate_message(message: str) -> None:
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValidationError(f'Message must be at most {MAX_MESSAGE_LENGTH} characters.')


def validate_new_file(transfer: Transfer, size: int, settings: FileTransferSettings) -> None:
    """Validate that adding one more file of `size` bytes to `transfer` stays within the
    logged-in limits. Call before creating the `TransferFile` / multipart upload."""
    if size <= 0:
        raise ValidationError('File is empty.')
    if size > settings.logged_in_max_file_size_bytes:
        limit_gb = settings.logged_in_max_file_size_bytes / 1024**3
        raise ValidationError(f'File is larger than the {limit_gb:.1f} GB limit per file.')

    existing = transfer.files.all()
    if existing.count() >= settings.logged_in_max_files:
        raise ValidationError(f'A transfer can have at most {settings.logged_in_max_files} files.')

    total = sum(f.size for f in existing) + size
    if total > settings.logged_in_max_total_size_bytes:
        limit_gb = settings.logged_in_max_total_size_bytes / 1024**3
        raise ValidationError(f'Transfer would exceed the {limit_gb:.1f} GB total size limit.')


def validate_recipients(emails: list[str], settings: FileTransferSettings) -> list[str]:
    """Deduplicate (case-insensitively) and validate a recipient list against the max-recipients
    limit. Returns the deduplicated list, preserving first-seen order and casing."""
    seen: set[str] = set()
    deduped: list[str] = []
    for email in emails:
        key = email.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(email.strip())

    if len(deduped) > settings.logged_in_max_recipients:
        raise ValidationError(
            f'A transfer can have at most {settings.logged_in_max_recipients} recipients.'
        )
    return deduped


def validate_has_files(transfer: Transfer) -> None:
    if not TransferFile.objects.filter(transfer=transfer, uploaded=True).exists():
        raise ValidationError('Add at least one file before sending.')
