"""`GET /api/receipts/export.csv`: the live receipts matching the filters, as CSV (the same
filters and columns as the dashboard's export, built by the same service)."""

from django.http import StreamingHttpResponse
from django.utils import timezone

from apps.core import ratelimit

from .. import services
from ..services.filters import DEFAULT_SORT
from .common import mapped_errors, receipt_filters
from .router import router


@router.get('/export.csv')
def export_csv(
    request,
    status: str | None = None,
    category: str | None = None,
    q: str = '',
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = DEFAULT_SORT,
    duplicates: bool = False,
):
    """Download the matching receipts as `text/csv` (same filters as `GET /receipts`; the Trash is
    never exported). Limited per user (`RECEIPTS_EXPORT_USER`), shared with the web export."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_EXPORT_USER'))
    filters = receipt_filters(
        status=status,
        category=category,
        q=q,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        duplicates=duplicates,
    )
    with mapped_errors():
        # Validate the filters now: the CSV is built lazily, and an error halfway through a
        # streaming body can't become a 400.
        services.filter_receipts(request.auth, filters)
    response = StreamingHttpResponse(
        services.iter_csv(request.auth, filters), content_type='text/csv; charset=utf-8'
    )
    filename = f'receipts-{timezone.localdate().isoformat()}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'private, no-store'
    return response
