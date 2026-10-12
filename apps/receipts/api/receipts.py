"""The receipts endpoints (`/api/receipts/receipts`): list, read, edit, trash and restore a
receipt, re-run its extraction, clear a duplicate flag and get a short-lived link to its file.

Every endpoint calls the same `apps.receipts.services` functions the web views do, so the same
validation and billing rules apply either way. See `common.py` for the status codes.
"""

from uuid import UUID

from ninja.errors import HttpError

from apps.core import ratelimit
from apps.core.services.storage import GET_URL_EXPIRES_SECONDS

from .. import services
from ..schemas import FileUrlSchema, ReceiptListSchema, ReceiptSchema, ReceiptUpdateSchema
from ..services.filters import DEFAULT_SORT, filter_receipts
from .common import (
    DEFAULT_LIMIT,
    clamp_page,
    mapped_errors,
    owned_receipt,
    receipt_filters,
)
from .router import router


@router.get('/receipts', response=ReceiptListSchema)
def list_receipts(
    request,
    status: str | None = None,
    category: str | None = None,
    vendor: int | None = None,
    q: str = '',
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = DEFAULT_SORT,
    duplicates: bool = False,
    trashed: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
):
    """List the caller's receipts (the live ones; `trashed=true` lists the Trash instead).

    - `status`: one or more of `received`, `processing`, `waiting_for_credits`, `needs_review`,
      `processed`, `failed`, comma-separated; `in_progress` stands for the first three.
    - `category`: a category id, or `none` for uncategorized.
    - `vendor`: a vendor id (`GET /vendors`); an id that isn't yours matches nothing.
    - `q`: matches the vendor (also by the name the AI read on the receipt), the notes and, for a
      number like `75`, the amount.
    - `date_from` / `date_to`: `YYYY-MM-DD`, inclusive, applied to `receipt_date`.
    - `sort`: `receipt_date`, `-receipt_date` (default), `created_at` or `-created_at`.
    - `duplicates`: only receipts flagged as possible duplicates.
    - `limit` (1-100, default 20) and `offset` page the result; `count` is the total.
    """
    filters = receipt_filters(
        status=status,
        category=category,
        vendor=vendor,
        q=q,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        duplicates=duplicates,
    )
    limit, offset = clamp_page(limit, offset)
    with mapped_errors():
        queryset = filter_receipts(request.auth, filters, trashed=trashed)
    queryset = queryset.select_related('category', 'vendor_ref')
    return {
        'count': queryset.count(),
        'limit': limit,
        'offset': offset,
        'results': list(queryset[offset : offset + limit]),
    }


@router.get('/receipts/{receipt_id}', response=ReceiptSchema)
def get_receipt(request, receipt_id: UUID):
    """One receipt, including one in the Trash (so it can be found again to restore)."""
    return owned_receipt(request, receipt_id)


@router.patch('/receipts/{receipt_id}', response=ReceiptSchema)
def update_receipt(request, receipt_id: UUID, payload: ReceiptUpdateSchema):
    """Edit a receipt. Only the fields present in the body are applied; the extracted fields you
    change are never overwritten by a later re-run, and a `needs_review` receipt becomes
    `processed` once vendor, amount and receipt date are all set.

    The vendor is `vendor` (a name) or `vendor_id` (one of yours), not both (400). Either moves
    this receipt to that vendor and teaches the matcher; it does not rename the vendor for its
    other receipts (`PATCH /vendors/{id}` does). A `vendor_id` that isn't yours is a 400."""
    receipt = owned_receipt(request, receipt_id)
    with mapped_errors():
        return services.update_receipt(receipt, payload.dict(exclude_unset=True))


@router.delete('/receipts/{receipt_id}', response={204: None})
def trash_receipt(request, receipt_id: UUID):
    """Move a receipt to the Trash (still stored and billed; restorable until it is purged)."""
    services.trash(owned_receipt(request, receipt_id))
    return 204, None


@router.post('/receipts/{receipt_id}/restore', response=ReceiptSchema)
def restore_receipt(request, receipt_id: UUID):
    """Take a receipt out of the Trash. Idempotent: restoring a live receipt changes nothing."""
    return services.restore(owned_receipt(request, receipt_id))


@router.post('/receipts/{receipt_id}/reprocess', response=ReceiptSchema)
def reprocess_receipt(request, receipt_id: UUID):
    """Run the extraction again (costs the per-file price again; edited fields and notes are
    kept). 402 if the account is frozen or the balance can't cover it."""
    receipt = owned_receipt(request, receipt_id)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_REPROCESS_USER'))
    with mapped_errors():
        return services.reprocess(receipt)


@router.post('/receipts/{receipt_id}/dismiss-duplicate', response=ReceiptSchema)
def dismiss_duplicate(request, receipt_id: UUID):
    """ "Not a duplicate": clear the flag and never raise it again for this receipt."""
    return services.dismiss_duplicate(owned_receipt(request, receipt_id))


@router.get('/receipts/{receipt_id}/file', response=FileUrlSchema)
def receipt_file(request, receipt_id: UUID, disposition: str = 'attachment'):
    """A short-lived presigned URL for the original file: `attachment` (default) downloads it,
    `inline` is for showing it in a browser (a PDF or image; a HEIC original is shown as its JPEG
    rendering). 402 while the account is frozen. Never cache the URL; ask again."""
    receipt = owned_receipt(request, receipt_id)
    if disposition not in ('inline', 'attachment'):
        raise HttpError(400, 'disposition must be inline or attachment.')
    with mapped_errors():
        url = (
            services.preview_url(receipt)
            if disposition == 'inline'
            else services.download_url(receipt)
        )
    return {
        'url': url,
        'disposition': disposition,
        'filename': services.file_name(receipt),
        'expires_in': GET_URL_EXPIRES_SECONDS,
    }
