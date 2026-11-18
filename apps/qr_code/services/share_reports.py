"""Abuse reports of shared QR codes (issue #5, PR 3): the public "report this QR code" form on a
shared page (`views.public.report_qrcode`), and their review in the admin (dismiss, take down;
`apps.qr_code.admin`). A copy of Notes' (`apps.notes.services.reports`): products may not import
each other.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.ratelimit import normalize_ip_for_key

from ..limits import MAX_DETAILS_LENGTH
from ..models import QRCode, QRShareLink, QRShareReport, ReportReason, ReportStatus
from .errors import ReportUnavailableError
from .share import is_available

#: A report from the same address against the same QR code inside this window is a repeat
#: submission (a double click, a reload), not a second, independent report.
DEDUPE_WINDOW = timedelta(minutes=10)
UNAVAILABLE_MESSAGE = ReportUnavailableError.MESSAGE
REPEAT_MESSAGE = 'You already reported this QR code recently. Thank you.'


def create_report(
    link: QRShareLink,
    *,
    reason: str,
    details: str = '',
    reporter_email: str = '',
    reporter_ip: str | None = None,
) -> QRShareReport:
    """Record a report against `link`'s QR code.

    The QR code's row is locked for the checks and the insert, so two reports from one address
    arriving together can't both pass the repeat check.

    Raises `ReportUnavailableError` (a `LinkUnavailableError`) when the link no longer works
    (revoked, expired, or its QR code deleted or taken down since the page was served), and
    `ValidationError` when `reason` isn't one of `ReportReason`, `details` is too long, or this is
    an obvious repeat (same normalised address, same QR code, within `DEDUPE_WINDOW`).
    """
    if reason not in ReportReason.values:
        raise ValidationError('Choose a reason for the report.')
    if len(details) > MAX_DETAILS_LENGTH:
        raise ValidationError(f'Details must be at most {MAX_DETAILS_LENGTH} characters.')

    with transaction.atomic():
        qrcode = (
            QRCode.objects.select_for_update()
            .select_related('created_by')
            .filter(pk=link.qrcode_id)
            .first()
        )
        fresh = QRShareLink.objects.filter(pk=link.pk).first()
        if qrcode is None or fresh is None:
            raise ReportUnavailableError
        fresh.qrcode = qrcode
        if not is_available(fresh):
            raise ReportUnavailableError

        if reporter_ip:
            # Compared as *normalised* keys (an IPv6 address collapsed to its /64, an IPv4-mapped
            # one unwrapped: `normalize_ip_for_key`, shared with rate limiting), so a visitor
            # whose IPv6 privacy address rotates between submissions can't dodge the window. Done
            # in Python: the window is ten minutes, so there are only a handful of rows.
            normalized = normalize_ip_for_key(reporter_ip)
            recent = QRShareReport.objects.filter(
                qrcode=qrcode,
                reporter_ip__isnull=False,
                created_at__gte=timezone.now() - DEDUPE_WINDOW,
            ).values_list('reporter_ip', flat=True)
            if any(normalize_ip_for_key(ip) == normalized for ip in recent):
                raise ValidationError(REPEAT_MESSAGE)

        return QRShareReport.objects.create(
            qrcode=qrcode,
            link=fresh,
            reason=reason,
            details=details,
            reporter_email=reporter_email,
            reporter_ip=reporter_ip,
        )


def dismiss_report(report: QRShareReport, *, by: User, note: str = '') -> bool:
    """Dismiss one pending report: no action needed on its QR code. Returns whether this call
    dismissed it (False when it had already been reviewed)."""
    now = timezone.now()
    changed = QRShareReport.objects.filter(pk=report.pk, status=ReportStatus.PENDING).update(
        status=ReportStatus.DISMISSED, reviewed_by=by, reviewed_at=now, resolution_note=note
    )
    if not changed:
        return False
    report.status = ReportStatus.DISMISSED
    report.reviewed_by = by
    report.reviewed_at = now
    report.resolution_note = note
    return True
