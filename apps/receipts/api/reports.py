"""`GET /api/receipts/reports`: receipts by vendor or by category in a period (issue #351), the
same report as the Reports page and its downloads (`services.reports`).

Strict, unlike the page: an unknown `group` or `date_range`, a malformed date, dates sent with a
preset and a first date after the last are a 400. The default period is this year, like the page.
Only processed receipts are counted unless `processed_only=false` (which adds the ones needing
review; failed and in-progress receipts are never counted), and the Trash never is. Limited per
user (`RECEIPTS_REPORT_USER`, shared with the web downloads), charged after the parameters are
checked, so a refused request spends nothing.
"""

from ninja.errors import HttpError

from apps.accounts.services.timezones import user_today
from apps.core import ratelimit
from apps.core.date_ranges import (
    DATE_RANGE_ANY,
    DATE_RANGE_CUSTOM,
    DATE_RANGES,
    resolve_date_range,
)
from apps.core.datetime_format import PUBLIC

from ..schemas.report import ReceiptReportSchema
from ..services.reports import GROUPS, ReportFilters, build_report, describe_filters
from .common import parse_date
from .router import router

DEFAULT_RANGE = 'this_year'
_RANGES = tuple(value for value, _label in DATE_RANGES)


def report_filters(
    user,
    *,
    group: str,
    date_range: str | None,
    date_from: str | None,
    date_to: str | None,
    processed_only: bool,
    vetted_only: bool,
) -> ReportFilters:
    if group not in GROUPS:
        raise HttpError(400, f'group must be one of: {", ".join(GROUPS)}.')
    first = parse_date('date_from', date_from)
    last = parse_date('date_to', date_to)
    if date_range is None:
        date_range = DATE_RANGE_CUSTOM if first or last else DEFAULT_RANGE
    if date_range not in _RANGES:
        raise HttpError(400, f'date_range must be one of: {", ".join(_RANGES)}.')
    if date_range == DATE_RANGE_CUSTOM:
        if first and last and first > last:
            raise HttpError(400, 'date_from must not be after date_to.')
    else:
        if first or last:
            raise HttpError(400, 'date_from and date_to go with date_range=custom.')
        if date_range == DATE_RANGE_ANY:
            first = last = None
        else:
            first, last = resolve_date_range(date_range, user_today(user))
    return ReportFilters(
        group=group,
        date_from=first,
        date_to=last,
        processed_only=processed_only,
        vetted_only=vetted_only,
    )


@router.get('/reports', response=ReceiptReportSchema)
def get_report(
    request,
    group: str = 'vendor',
    date_range: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    processed_only: bool = True,
    vetted_only: bool = False,
):
    """Receipts by `group` (`vendor` or `category`) in a period: `date_range` is one of `any`,
    `last_15_days`, `last_30_days`, `this_month`, `this_quarter`, `this_year` (the default) or
    `custom`, which takes `date_from` / `date_to` as `YYYY-MM-DD` (inclusive, either may be left
    out); presets are resolved against the caller's today. `vetted_only=true` keeps only vendors
    the user confirmed. Amounts in different currencies are never added together: each row is a
    vendor (or category) in one currency, and there is one total per currency."""
    filters = report_filters(
        request.auth,
        group=group,
        date_range=date_range,
        date_from=date_from,
        date_to=date_to,
        processed_only=processed_only,
        vetted_only=vetted_only,
    )
    ratelimit.enforce(ratelimit.hit_user(request.auth, 'RECEIPTS_REPORT_USER'))
    report = build_report(request.auth, filters)
    return {
        'group': filters.group,
        'date_from': filters.date_from,
        'date_to': filters.date_to,
        'processed_only': filters.processed_only,
        'vetted_only': filters.vetted_only,
        'description': describe_filters(filters, PUBLIC),
        'receipt_count': report.receipt_count,
        'rows': [
            {
                'id': row.key,
                'name': row.name,
                'vetted': row.vetted,
                'currency': row.currency,
                'receipt_count': row.count,
                'total': row.total,
                'share': row.share,
                'average': row.average,
                'last_receipt_date': row.last_date,
            }
            for row in report.rows
        ],
        'totals': [
            {
                'currency': total.currency,
                'receipt_count': total.count,
                'total': total.total,
                'average': total.average,
            }
            for total in report.totals
        ],
    }
