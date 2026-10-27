"""Abuse reports (issue #162): the public "report this link" form on the preview page
(`views.report`), and their review in the admin (dismiss, take down; `apps.short_url.admin`).
Modelled on file transfer's (`apps.file_transfer.services.reports`).
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.ratelimit import normalize_ip_for_key

from ..models import (
    MAX_DETAILS_LENGTH,
    LinkReport,
    LinkStatus,
    ReportReason,
    ReportStatus,
    ShortLink,
)
from .hold import maybe_hold_for_reports, release_hold

#: A report from the same address against the same link inside this window is a repeat
#: submission (a double click, a reload), not a second, independent report.
DEDUPE_WINDOW = timedelta(minutes=10)


def create_report(
    link: ShortLink,
    *,
    reason: str,
    details: str = '',
    reporter_email: str = '',
    reporter_ip: str | None = None,
) -> LinkReport:
    """Record a report against `link` and check whether it just crossed the auto-hold threshold
    (`hold.maybe_hold_for_reports`).

    The link's row is locked for the insert and the hold check, so two reports arriving together
    each count the other (without it both could see one pending report and neither would hold).

    Raises `ValidationError` when `reason` isn't one of `ReportReason`, `details` is too long, the
    link has ended, or this is an obvious repeat (same normalized address, same link, within
    `DEDUPE_WINDOW`).
    """
    if reason not in ReportReason.values:
        raise ValidationError('Choose a reason for the report.')
    if len(details) > MAX_DETAILS_LENGTH:
        raise ValidationError(f'Details must be at most {MAX_DETAILS_LENGTH} characters.')

    with transaction.atomic():
        locked = ShortLink.objects.select_for_update().filter(pk=link.pk).first()
        if locked is None or locked.status not in (LinkStatus.ACTIVE, LinkStatus.DISABLED):
            raise ValidationError('This link is no longer available.')

        if reporter_ip:
            # Compared as *normalized* keys (an IPv6 address collapsed to its /64, an IPv4-mapped
            # one unwrapped: `normalize_ip_for_key`, shared with rate limiting), so a visitor
            # whose IPv6 privacy address rotates between submissions can't dodge the window.
            # Done in Python: the window is ten minutes, so there are only a handful of rows.
            normalized = normalize_ip_for_key(reporter_ip)
            recent = LinkReport.objects.filter(
                link=locked,
                reporter_ip__isnull=False,
                created_at__gte=timezone.now() - DEDUPE_WINDOW,
            ).values_list('reporter_ip', flat=True)
            if any(normalize_ip_for_key(ip) == normalized for ip in recent):
                raise ValidationError('You already reported this link recently. Thank you.')

        report = LinkReport.objects.create(
            link=locked,
            reason=reason,
            details=details,
            reporter_email=reporter_email,
            reporter_ip=reporter_ip,
        )
        maybe_hold_for_reports(locked)
    link.held_for_review_at = locked.held_for_review_at
    return report


def dismiss_report(report: LinkReport, *, by: User, note: str = '') -> bool:
    """Dismiss one pending report: no action needed on its link. When it was the last pending
    report against a link on hold, the hold is released too. Returns whether this call dismissed
    it (False when it had already been reviewed)."""
    with transaction.atomic():
        link = ShortLink.objects.select_for_update().filter(pk=report.link_id).first()
        now = timezone.now()
        changed = LinkReport.objects.filter(pk=report.pk, status=ReportStatus.PENDING).update(
            status=ReportStatus.DISMISSED, reviewed_by=by, reviewed_at=now, resolution_note=note
        )
        if not changed:
            return False
        report.status = ReportStatus.DISMISSED
        report.reviewed_by = by
        report.reviewed_at = now
        report.resolution_note = note
        if link is not None:
            _release_hold_if_clear(link)
    return True


def _release_hold_if_clear(link: ShortLink) -> None:
    if not link.held_for_review_at:
        return
    if not link.reports.filter(status=ReportStatus.PENDING).exists():
        release_hold(link)


def release_hold_and_dismiss(link: ShortLink, *, by: User, note: str = '') -> None:
    """Staff release a hold: it is lifted and the pending reports behind it are dismissed, so the
    very next report doesn't hold the link again at once."""
    with transaction.atomic():
        release_hold(link)
        link.reports.filter(status=ReportStatus.PENDING).update(
            status=ReportStatus.DISMISSED,
            reviewed_by=by,
            reviewed_at=timezone.now(),
            resolution_note=note,
        )
