"""Abuse reports (issue #59): the public "report this transfer" form on the download page
(`views.download.report_transfer`), and their review in the admin (dismiss / take down, in
`apps.file_transfer.admin`).
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.core.ratelimit import normalize_ip_for_key

from ..models import MAX_DETAILS_LENGTH, AbuseReport, AbuseReportReason, AbuseReportStatus, Transfer
from .hold import maybe_hold_for_reports, release_hold

#: Dedupe window (issue #59: "dedupe obvious repeats (same IP + transfer within a window) if
#: cheap"). A report from the same IP against the same transfer inside this window is treated as
#: a repeat submission (double-click, page reload) rather than a second, independent report.
_DEDUPE_WINDOW = timedelta(minutes=10)


def create_report(
    transfer: Transfer,
    *,
    reason: str,
    details: str = '',
    reporter_email: str = '',
    reporter_ip: str | None = None,
) -> AbuseReport:
    """Record a new report against `transfer` and check whether it just crossed the auto-hold
    threshold (`services.hold.maybe_hold_for_reports`).

    Raises:
        ValidationError: `reason` isn't one of `AbuseReportReason`'s values, `details` is too
            long, or this looks like an obvious repeat (same IP, same transfer, within the dedupe
            window).
    """
    if reason not in AbuseReportReason.values:
        raise ValidationError('Choose a reason for the report.')
    if len(details) > MAX_DETAILS_LENGTH:
        raise ValidationError(f'Details must be at most {MAX_DETAILS_LENGTH} characters.')

    if reporter_ip:
        # Compares *normalized* keys (an IPv6 address collapsed to its /64, an IPv4-mapped IPv6
        # address unwrapped to plain IPv4 -- `apps.core.ratelimit.normalize_ip_for_key`, shared
        # with rate limiting) rather than the exact stored addresses: otherwise the same visitor
        # reloading the page over IPv6 privacy-extension address rotation, or hitting an IPv4 vs.
        # `::ffff:`-mapped path, would dodge the dedupe window entirely. Done in Python rather than
        # in the query -- the window is only 10 minutes, so there are at most a handful of rows to
        # check, the same "stays small" trade-off `services.blocklist` makes for its own IP scan.
        cutoff = timezone.now() - _DEDUPE_WINDOW
        normalized = normalize_ip_for_key(reporter_ip)
        recent_ips = AbuseReport.objects.filter(
            transfer=transfer, reporter_ip__isnull=False, created_at__gte=cutoff
        ).values_list('reporter_ip', flat=True)
        if any(normalize_ip_for_key(ip) == normalized for ip in recent_ips):
            raise ValidationError('You already reported this transfer recently. Thank you.')

    report = AbuseReport.objects.create(
        transfer=transfer,
        reason=reason,
        details=details,
        reporter_email=reporter_email,
        reporter_ip=reporter_ip,
    )
    maybe_hold_for_reports(transfer)
    return report


def dismiss_report(report: AbuseReport, *, by: User, note: str = '') -> AbuseReport:
    """Dismiss one report: no action needed on its transfer. If this was the last pending report
    against a transfer currently on hold, releases the hold too (issue #59: "admin can release the
    hold (dismiss reports)")."""
    now = timezone.now()
    report.status = AbuseReportStatus.DISMISSED
    report.reviewed_by = by
    report.reviewed_at = now
    report.resolution_note = note
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'resolution_note'])
    _release_hold_if_clear(report.transfer)
    return report


def _release_hold_if_clear(transfer: Transfer) -> None:
    if not transfer.held_for_review_at:
        return
    if not transfer.reports.filter(status=AbuseReportStatus.PENDING).exists():
        release_hold(transfer)
