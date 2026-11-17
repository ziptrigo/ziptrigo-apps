"""The file export endpoints (`/api/receipts/exports`, issue #315): "download all my files" as
zip files, for the API and the CLI. The twin of the account page's "Download files" dialog and the
exports pages (`apps.receipts.views.exports`): the same services, the same rate limits and budgets.

1. `POST /exports/quote` says what an export of some filters would be (files, size, zip files,
   cost). Nothing is written or charged.
2. `POST /exports` charges for and starts it (201). It is built in the background: poll
   `GET /exports/{id}` until `status` is `ready` (or `failed`, which refunds it).
3. `GET /exports/{id}/parts/{number}/url` gives a short-lived presigned URL per zip file; `GET` it
   without the `Authorization` header.
4. `DELETE /exports/{id}` removes the zip files at once (nothing is refunded).

See `common.py` for the status codes. A refused request spends none of its rate-limit budget
wherever the refusal needs no write: the filters and layout, the frozen account, another export
being built, an empty selection, a changed cost and a balance that can't pay are all told before
`RECEIPTS_EXPORT_FILES_USER` is charged.
"""

from uuid import UUID

from django.core.exceptions import ValidationError
from ninja.errors import HttpError

from apps.core import ratelimit
from apps.core.services.storage import GET_URL_EXPIRES_SECONDS

from .. import services
from ..models import ExportStatus, ReceiptExport
from ..schemas import (
    ReceiptExportCreateSchema,
    ReceiptExportListSchema,
    ReceiptExportPartUrlSchema,
    ReceiptExportQuoteSchema,
    ReceiptExportRequestSchema,
    ReceiptExportSchema,
)
from ..services.exports import clean_layout, part_name
from ..services.filters import DEFAULT_SORT, ReceiptFilters
from .common import (
    DEFAULT_LIMIT,
    clamp_page,
    export_data,
    mapped_errors,
    owned_export,
    receipt_filters,
)
from .router import router


def _selection(request, payload: ReceiptExportRequestSchema) -> tuple[ReceiptFilters, str]:
    """The body's filters and layout, validated (a 400 for anything the services refuse), before
    any rate limit is charged."""
    filters = receipt_filters(
        status=payload.status,
        category=payload.category,
        vendor=payload.vendor,
        q=payload.q,
        date_from=payload.date_from,
        date_to=payload.date_to,
        sort=DEFAULT_SORT,
        duplicates=payload.duplicates,
    )
    with mapped_errors():
        layout = clean_layout(payload.layout)
        # Validate the filters now (the list is built lazily).
        services.filter_receipts(request.auth, filters)
    return filters, layout


@router.post('/exports/quote', response=ReceiptExportQuoteSchema)
def quote_export(request, payload: ReceiptExportRequestSchema):
    """What an export of these filters would be: how many files, their size, how many zip files,
    the cost in credits, your balance and whether it is `affordable`. Writes and charges nothing.
    The quote reads the whole filtered list, so it is limited per user
    (`RECEIPTS_EXPORT_DIALOG_USER`), the budget the website's dialog uses; a request with invalid
    filters or layout is a 400 and spends none of it."""
    filters, layout = _selection(request, payload)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_EXPORT_DIALOG_USER'))
    with mapped_errors():
        return services.quote_export(request.auth, filters, layout)


@router.post('/exports', response={201: ReceiptExportSchema})
def create_export(request, payload: ReceiptExportCreateSchema):
    """Charge for and start an export: every live receipt that has a stored file and matches the
    filters, packed into independent zip files (about `part_bytes` each) with a `receipts.csv` of
    their data in number 1. Costs `price_per_file` credits per file, charged now; kept for
    `retention_days` once ready. Answers 201 with the export, `status: building`.

    Send `expected_cost` (the quote's `cost`) to be sure of what you pay: if the cost is now
    different the answer is a 409 naming the real total, and nothing is charged. Without it the
    export is created whatever it costs.

    400 for a bad layout or filter or when no file matches; 402 while the account is frozen or the
    balance can't pay; 409 while another export is being prepared (one at a time) or the cost
    changed; 429 over `RECEIPTS_EXPORT_FILES_USER`, which a refused request does not spend, or over
    `RECEIPTS_EXPORT_DIALOG_USER`, which the whole-list read of a refused request spends."""
    filters, layout = _selection(request, payload)
    # `check_export` reads and plans the whole list, so it is bounded like the quote is: over the
    # dialog budget nothing is read; a refusal (which still read the list) spends one hit of it.
    # A refusal spends none of `RECEIPTS_EXPORT_FILES_USER`.
    ratelimit.enforce(ratelimit.peek_user(request.auth, 'RECEIPTS_EXPORT_DIALOG_USER'))
    try:
        with mapped_errors():
            prepared = services.check_export(
                request.auth, filters, layout, expected_cost=payload.expected_cost
            )
    except HttpError:
        ratelimit.hit_user(request.auth, 'RECEIPTS_EXPORT_DIALOG_USER')
        raise
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_EXPORT_FILES_USER'))
    with mapped_errors():
        export = services.create_export(
            request.auth,
            filters,
            layout,
            expected_cost=payload.expected_cost,
            prepared=prepared,
        )
    return 201, export_data(export)


@router.get('/exports', response=ReceiptExportListSchema)
def list_exports(request, limit: int = DEFAULT_LIMIT, offset: int = 0):
    """The caller's exports, newest first (a deleted one is not listed). `limit` (1-100, default
    20) and `offset` page the result; `count` is the total."""
    limit, offset = clamp_page(limit, offset)
    queryset = (
        ReceiptExport.objects.filter(user=request.auth)
        .exclude(status=ExportStatus.DELETED)
        .order_by('-created_at', '-pk')
        .prefetch_related('parts')
    )
    return {
        'count': queryset.count(),
        'limit': limit,
        'offset': offset,
        'results': [export_data(export) for export in queryset[offset : offset + limit]],
    }


@router.get('/exports/{export_id}', response=ReceiptExportSchema)
def get_export(request, export_id: UUID):
    """One export: its status, the zip files (`parts`, with `built` while they are being
    written), what was charged and refunded, what it was made from and when it expires."""
    return export_data(owned_export(request, export_id))


@router.get('/exports/{export_id}/parts/{number}/url', response=ReceiptExportPartUrlSchema)
def export_part_url(request, export_id: UUID, number: int):
    """A short-lived presigned URL for one zip file of a ready export (`GET` it with no
    `Authorization` header; it downloads as `filename`). Ask again for each download. 400 for an
    export that is not ready, 402 while the account is frozen, 404 for a part number the export
    doesn't have; limited per user (`RECEIPTS_EXPORT_DOWNLOAD_USER`, the website's budget), which
    a refused request does not spend."""
    export = owned_export(request, export_id)
    with mapped_errors():
        url, part = services.part_download(export, number)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_EXPORT_DOWNLOAD_USER'))
    return {
        'url': url,
        'filename': part_name(export, number),
        'size_bytes': part.size_bytes,
        'expires_in': GET_URL_EXPIRES_SECONDS,
    }


@router.delete('/exports/{export_id}', response={204: None})
def delete_export(request, export_id: UUID):
    """Delete an export: its zip files go now (nothing is refunded). 409 while it is still being
    prepared; a deleted export is a 404."""
    export = owned_export(request, export_id)
    try:
        services.delete_export(export)
    except ValidationError as exc:
        raise HttpError(409, exc.messages[0]) from exc
    return 204, None
