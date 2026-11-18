"""Forgetting old IP addresses (issue #5 follow-up): the one retention rule for the IP a visitor
left on a public report, shared by the products that store one (QR codes, Notes, Short URLs).

`core` can't import a product, so each product's own daily job calls `clear_old_ips` with its
model; the rule (how long, how many rows per statement) lives here. File transfer's download
log keeps the same 90 days (`file_transfer.jobs.purge_download_ips`).
"""

from datetime import timedelta

from django.db.models import QuerySet
from django.utils import timezone

#: How long a report keeps the IP address it was sent from.
IP_RETENTION = timedelta(days=90)

#: Rows changed per `UPDATE`, so one run never holds a long lock on a big table.
BATCH = 1000


def clear_old_ips(
    queryset: QuerySet,
    *,
    field: str = 'reporter_ip',
    date_field: str = 'created_at',
    older_than: timedelta = IP_RETENTION,
    batch: int = BATCH,
) -> int:
    """Set `field` to `NULL` on the rows of `queryset` whose `date_field` is `older_than` ago or
    more, `batch` rows per statement. Returns how many rows were changed. Idempotent: a row
    already cleared is not selected again. Errors propagate (the scheduler records them)."""
    cutoff = timezone.now() - older_than
    due = queryset.filter(**{f'{date_field}__lte': cutoff, f'{field}__isnull': False})
    total = 0
    while True:
        ids = list(due.order_by('pk').values_list('pk', flat=True)[:batch])
        if not ids:
            return total
        total += due.filter(pk__in=ids).update(**{field: None})
