"""Serving a receipt's stored file: presigned GET URLs, and the raw body of an HTML email.

All of it is refused while the account is frozen (`AccountFrozenError`): no previews or downloads
until the storage is paid for. Nothing is ever deleted by freezing.

Inline display (`disposition='inline'`) is allowed only for types a browser renders without running
script (`apps.core.services.storage.INLINE_CONTENT_TYPES`); a HEIC original is shown through its
JPEG `preview_key` instead. An HTML email body is *never* served by S3: a Django view streams it
with a strict CSP into a sandboxed iframe (a later release), reading it with `get_body_bytes`.
"""

from django.core.exceptions import ValidationError

from apps.core.services.storage import INLINE_CONTENT_TYPES, Disposition

from ..models import Receipt
from .accounts import is_frozen
from .documents import HEIC, JPEG
from .errors import AccountFrozenError
from .storage import get_storage

#: Largest email body (HTML or text) that `get_body_bytes` will load into memory.
MAX_BODY_BYTES = 5 * 1024 * 1024


def _require_available(receipt: Receipt) -> None:
    if is_frozen(receipt.user):
        raise AccountFrozenError()
    if not receipt.uploaded or not receipt.file_key:
        raise ValidationError('There is no file for this receipt.')


def _default_name(receipt: Receipt) -> str:
    """A name for a file that has none (an email body): `receipt` plus the right extension, so
    it opens in the right program when saved."""
    return {'text/html': 'email.html', 'text/plain': 'email.txt'}.get(
        receipt.content_type, 'receipt'
    )


def download_url(receipt: Receipt) -> str:
    """A short-lived "Save As" URL for the original file."""
    _require_available(receipt)
    return get_storage().presigned_get_url(
        receipt.file_key,
        receipt.original_filename or _default_name(receipt),
        disposition='attachment',
    )


def preview_url(receipt: Receipt) -> str:
    """A short-lived URL to show the receipt inline in the browser: the original for a PDF or a
    JPEG/PNG/WebP image, the stored JPEG rendering for a HEIC one.

    Raises:
        ValidationError: there is nothing a browser can show inline (an HTML email body is shown
            by its own view, not through S3).
    """
    _require_available(receipt)
    if receipt.content_type == HEIC:
        if not receipt.preview_key:
            raise ValidationError('The preview is not ready yet.')
        key, content_type = receipt.preview_key, JPEG
    elif receipt.content_type in INLINE_CONTENT_TYPES:
        key, content_type = receipt.file_key, receipt.content_type
    else:
        raise ValidationError('This receipt has no inline preview.')
    disposition: Disposition = 'inline'
    return get_storage().presigned_get_url(
        key,
        receipt.original_filename or 'receipt',
        disposition=disposition,
        content_type=content_type,
    )


def get_body_bytes(receipt: Receipt) -> bytes:
    """The stored bytes of an email-body receipt (HTML or text), for the CSP-locked body view."""
    _require_available(receipt)
    return get_storage().get_object_bytes(receipt.file_key, max_bytes=MAX_BODY_BYTES)
