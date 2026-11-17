"""The file export endpoints (`/api/todo/exports`, issue #326): "download all my task files" as zip
files, and the quick spreadsheet, for the API and the CLI. The twin of the account page's
"Download files" dialog and the exports pages (`apps.todo.views.exports`), and of Receipts' export
API (`apps.receipts.api.exports`): the same services, the same rate limits and budgets.

1. `POST /exports/quote` says what an export of a created-date range would be (files, size, zip
   files, cost). Nothing is written or charged.
2. `POST /exports` charges for and starts it (201). It is built in the background: poll
   `GET /exports/{id}` until `status` is `ready` (or `failed`, which refunds it).
3. `GET /exports/{id}/parts/{number}/url` gives a short-lived presigned URL per zip file; `GET` it
   without the `Authorization` header.
4. `DELETE /exports/{id}` removes the zip files at once (nothing is refunded).
5. `GET /export.xlsx` is the free quick workbook of the tasks, with no files.

See `common.py` for the status codes. A refused request spends none of its rate-limit budget
wherever the refusal needs no write: the range, the frozen account, another export being built,
an empty selection, a changed cost and a balance that can't pay are all told before
`TODO_EXPORT_FILES_USER` is charged.
"""

import tempfile
from uuid import UUID

from django.core.exceptions import ValidationError
from django.http import FileResponse
from ninja.errors import HttpError

from apps.accounts.services.timezones import user_today
from apps.core import ratelimit
from apps.core.services.storage import GET_URL_EXPIRES_SECONDS

from ..models import ExportStatus, TodoExport
from ..schemas import (
    TodoExportCreateSchema,
    TodoExportListSchema,
    TodoExportPartUrlSchema,
    TodoExportQuoteSchema,
    TodoExportRequestSchema,
    TodoExportSchema,
)
from ..services import exports as services
from ..services.export_range import ExportRange
from ..services.export_workbook import XLSX_CONTENT_TYPE
from .common import (
    DEFAULT_LIMIT,
    clamp_page,
    export_data,
    export_range,
    mapped_errors,
    owned_export,
)
from .router import router


def _range(payload: TodoExportRequestSchema) -> ExportRange:
    return export_range(payload.date_range, payload.date_from, payload.date_to)


@router.get('/export.xlsx')
def export_xlsx(
    request,
    date_range: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Download the tasks **created** in the range as a spreadsheet (`tasks.xlsx`: a Tasks and a
    Comments sheet, file names in the files columns; nothing stored). Free. The range is as for
    `POST /exports/quote`. Limited per user (`TODO_EXPORT_XLSX_USER`), shared with the web
    export; a request with an invalid range is a 400 and does not spend any of that budget."""
    chosen = export_range(date_range, date_from, date_to)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'TODO_EXPORT_XLSX_USER'))
    # The workbook is a zip itself, so it is written to a real file (spilling to disk when big)
    # and streamed from there; closing the response removes it.
    target = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    try:
        services.build_xlsx(request.auth, chosen, target)
    except Exception:
        target.close()  # only the response closes it otherwise: don't wait for the collector
        raise
    target.seek(0)
    response = FileResponse(
        target,
        as_attachment=True,
        filename=f'tasks-{user_today(request.auth).isoformat()}.xlsx',
        content_type=XLSX_CONTENT_TYPE,
    )
    response['Cache-Control'] = 'private, no-store'
    return response


@router.post('/exports/quote', response=TodoExportQuoteSchema)
def quote_export(request, payload: TodoExportRequestSchema):
    """What an export of the tasks created in this range would be: how many files, their size, how
    many zip files, the cost in credits, your balance and whether it is `affordable`. Writes and
    charges nothing. The quote reads every file in the range, so it is limited per user
    (`TODO_EXPORT_DIALOG_USER`), the budget the website's dialog uses; a request with an invalid
    range is a 400 and spends none of it."""
    chosen = _range(payload)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'TODO_EXPORT_DIALOG_USER'))
    with mapped_errors():
        return services.quote_export(request.auth, chosen)


@router.post('/exports', response={201: TodoExportSchema})
def create_export(request, payload: TodoExportCreateSchema):
    """Charge for and start an export: every uploaded file on a task created in the range (and on
    its comments that are not deleted; never the Trash), packed into independent zip files (about
    `part_bytes` each) with a `tasks.xlsx` of the tasks and comments in number 1. Costs
    `price_per_file` credits per file, charged now; kept for `retention_days` once ready. Answers
    201 with the export, `status: building`.

    Send `expected_cost` (the quote's `cost`) to be sure of what you pay: if the cost is now
    different the answer is a 409 naming the real total, and nothing is charged. Without it the
    export is created whatever it costs.

    400 for a bad range or when no file matches (use `GET /export.xlsx` for the task list); 402
    while the account is frozen or the balance can't pay; 409 while another export is being
    prepared (one at a time) or the cost changed; 429 over `TODO_EXPORT_FILES_USER`, which a
    refused request does not spend, or over `TODO_EXPORT_DIALOG_USER`, which the whole-range read
    of a refused request spends."""
    chosen = _range(payload)
    # `check_export` reads and plans every file in the range, so it is bounded like the quote is:
    # over the dialog budget nothing is read; a refusal (which still read them) spends one hit of
    # it. A refusal spends none of `TODO_EXPORT_FILES_USER`.
    ratelimit.enforce(ratelimit.peek_user(request.auth, 'TODO_EXPORT_DIALOG_USER'))
    try:
        with mapped_errors():
            prepared = services.check_export(
                request.auth, chosen, expected_cost=payload.expected_cost
            )
    except HttpError:
        ratelimit.hit_user(request.auth, 'TODO_EXPORT_DIALOG_USER')
        raise
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'TODO_EXPORT_FILES_USER'))
    with mapped_errors():
        export = services.create_export(
            request.auth, chosen, expected_cost=payload.expected_cost, prepared=prepared
        )
    return 201, export_data(export)


@router.get('/exports', response=TodoExportListSchema)
def list_exports(request, limit: int = DEFAULT_LIMIT, offset: int = 0):
    """The caller's exports, newest first (a deleted one is not listed). `limit` (1-100, default
    50) and `offset` page the result; `count` is the total."""
    limit, offset = clamp_page(limit, offset)
    queryset = (
        TodoExport.objects.filter(user=request.auth)
        .exclude(status=ExportStatus.DELETED)
        .order_by('-created_at', '-pk')
        .select_related('user')  # the part names are dated in the user's time zone
        .prefetch_related('parts')
    )
    return {
        'count': queryset.count(),
        'limit': limit,
        'offset': offset,
        'results': [export_data(export) for export in queryset[offset : offset + limit]],
    }


@router.get('/exports/{export_id}', response=TodoExportSchema)
def get_export(request, export_id: UUID):
    """One export: its status, the zip files (`parts`, with `built` while they are being
    written), what was charged and refunded, what it was made from and when it expires."""
    return export_data(owned_export(request, export_id))


@router.get('/exports/{export_id}/parts/{number}/url', response=TodoExportPartUrlSchema)
def export_part_url(request, export_id: UUID, number: int):
    """A short-lived presigned URL for one zip file of a ready export (`GET` it with no
    `Authorization` header; it downloads as `filename`). Ask again for each download. 400 for an
    export that is not ready, 402 while the account is frozen, 404 for a part number the export
    doesn't have; limited per user (`TODO_EXPORT_DOWNLOAD_USER`, the website's budget), which a
    refused request does not spend."""
    export = owned_export(request, export_id)
    with mapped_errors():
        url, part = services.part_download(export, number)
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'TODO_EXPORT_DOWNLOAD_USER'))
    return {
        'url': url,
        'filename': services.part_name(export, number),
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
