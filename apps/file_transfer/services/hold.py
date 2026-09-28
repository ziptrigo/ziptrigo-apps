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

from apps.core.ratelimit import normalize_ip_for_key

from ..models import AbuseReportStatus, FileTransferSettings, Transfer


def maybe_hold_for_reports(
    transfer: Transfer, settings_row: FileTransferSettings | None = None
) -> bool:
    """Put `transfer` on hold once it has `auto_hold_report_threshold` (or more) pending reports
    from distinct IPs. A no-op (returns `False`) when the setting is off (0, the default), the
    transfer is already held, has already ended, or hasn't reached the threshold yet.

    Counts distinct *normalized* IPs (`apps.core.ratelimit.normalize_ip_for_key` -- an IPv6
    address collapsed to its /64, an IPv4-mapped IPv6 address unwrapped to plain IPv4) rather than
    raw report rows or exact stored addresses, so one visitor mashing "report" several times can't
    hold a transfer alone, and one host can't reach the threshold alone just by rotating its
    address within its own /64 -- see `services.reports.create_report`'s own dedupe window for the
    even cheaper case of the same (normalized) IP reporting the same transfer twice in quick
    succession. A report with no IP at all never counts towards the threshold. **Caveat**: this
    only raises the bar, it doesn't remove it -- anyone genuinely controlling N distinct real IPs
    (N normalized /64s, in the IPv6 case) can still trigger a hold alone, so
    `auto_hold_report_threshold` should be picked with that in mind rather than treated as a hard
    guarantee of N distinct reporters.
    """
    settings_row = settings_row or FileTransferSettings.load()
    threshold = settings_row.auto_hold_report_threshold
    if not threshold or transfer.held_for_review_at or transfer.is_ended:
        return False

    reporter_ips = transfer.reports.filter(
        status=AbuseReportStatus.PENDING, reporter_ip__isnull=False
    ).values_list('reporter_ip', flat=True)
    distinct_ips = len({normalize_ip_for_key(ip) for ip in reporter_ips})
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
    "release hold" action). Idempotent.

    Resets the billing clock to now, same as `services.metering.reenable_transfer` does for a
    suspended transfer coming back: `meter_transfer` simply doesn't advance `last_billed_at` while
    held (see its own docstring), so without this reset the held stretch would otherwise be billed
    for on the very next metering tick -- an extra or manual `meter_transfers` run must not charge
    for days spent on hold.
    """
    now = timezone.now()
    Transfer.objects.filter(pk=transfer.pk).update(held_for_review_at=None, last_billed_at=now)
    transfer.held_for_review_at = None
    transfer.last_billed_at = now
    return transfer
