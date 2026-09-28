"""Auto-hold a transfer under active abuse review (issue #59, spec section 13's "optional:
automatically suspend a transfer after N reports pending review" -- built here). See
`Transfer.held_for_review_at`'s docstring for why this is a plain flag rather than a status: it
must survive both the `credits_added` signal and the metering grace-period job untouched, neither
of which know anything about abuse review.

`maybe_hold_for_reports` is called once per new report (`services.reports.create_report`);
`release_hold` is called when staff dismiss the reports that caused a hold
(`services.reports.dismiss_report`) or explicitly release it from the admin.
"""

from django.utils import timezone

from ..models import AbuseReportStatus, FileTransferSettings, Transfer


def maybe_hold_for_reports(
    transfer: Transfer, settings_row: FileTransferSettings | None = None
) -> bool:
    """Put `transfer` on hold once it has `auto_hold_report_threshold` (or more) pending reports
    from distinct IPs. A no-op (returns `False`) when the setting is off (0, the default), the
    transfer is already held, has already ended, or hasn't reached the threshold yet.

    Counts distinct *IPs* rather than raw report rows, so one visitor mashing "report" several
    times can't hold a transfer alone -- see `services.reports.create_report`'s own dedupe window
    for the even cheaper case of the same IP reporting the same transfer twice in quick
    succession. A report with no IP at all never counts towards the threshold.
    """
    settings_row = settings_row or FileTransferSettings.load()
    threshold = settings_row.auto_hold_report_threshold
    if not threshold or transfer.held_for_review_at or transfer.is_ended:
        return False

    distinct_ips = (
        transfer.reports.filter(status=AbuseReportStatus.PENDING, reporter_ip__isnull=False)
        .values_list('reporter_ip', flat=True)
        .distinct()
        .count()
    )
    if distinct_ips < threshold:
        return False

    now = timezone.now()
    updated = Transfer.objects.filter(pk=transfer.pk, held_for_review_at__isnull=True).update(
        held_for_review_at=now
    )
    if updated:
        transfer.held_for_review_at = now
    return bool(updated)


def release_hold(transfer: Transfer) -> Transfer:
    """Release a transfer's abuse-review hold (staff dismissed the reports, or an explicit admin
    "release hold" action). Idempotent."""
    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=None)
    transfer.held_for_review_at = None
    return transfer
