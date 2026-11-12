"""Abuse reports (issue #260, PR 4): the public "report this note" form on a shared page
(`views.public.report_note`), and their review in the admin (dismiss, take down;
`apps.notes.admin`). Modelled on Short URLs' (`apps.short_url.services.reports`), without the
auto-hold.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.ratelimit import normalize_ip_for_key

from ..models import (
    MAX_DETAILS_LENGTH,
    Note,
    NoteReport,
    ReportReason,
    ReportStatus,
    ShareLink,
)
from .errors import ReportUnavailableError
from .links import is_available

#: A report from the same address against the same note inside this window is a repeat
#: submission (a double click, a reload), not a second, independent report.
DEDUPE_WINDOW = timedelta(minutes=10)
UNAVAILABLE_MESSAGE = ReportUnavailableError.MESSAGE
REPEAT_MESSAGE = 'You already reported this note recently. Thank you.'


def create_report(
    link: ShareLink,
    *,
    reason: str,
    details: str = '',
    reporter_email: str = '',
    reporter_ip: str | None = None,
) -> NoteReport:
    """Record a report against `link`'s note.

    The note's row is locked for the checks and the insert, so two reports from one address
    arriving together can't both pass the repeat check.

    Raises `ReportUnavailableError` (a `LinkUnavailableError`) when the link no longer works
    (revoked, expired, or its note trashed, locked or taken down since the page was served), and
    `ValidationError` when `reason` isn't one of `ReportReason`, `details` is too long, or this is
    an obvious repeat (same normalised address, same note, within `DEDUPE_WINDOW`).
    """
    if reason not in ReportReason.values:
        raise ValidationError('Choose a reason for the report.')
    if len(details) > MAX_DETAILS_LENGTH:
        raise ValidationError(f'Details must be at most {MAX_DETAILS_LENGTH} characters.')

    with transaction.atomic():
        note = Note.objects.select_for_update().filter(pk=link.note_id).first()
        fresh = ShareLink.objects.filter(pk=link.pk).first()
        if note is None or fresh is None:
            raise ReportUnavailableError
        fresh.note = note
        if not is_available(fresh):
            raise ReportUnavailableError

        if reporter_ip:
            # Compared as *normalised* keys (an IPv6 address collapsed to its /64, an IPv4-mapped
            # one unwrapped: `normalize_ip_for_key`, shared with rate limiting), so a visitor
            # whose IPv6 privacy address rotates between submissions can't dodge the window. Done
            # in Python: the window is ten minutes, so there are only a handful of rows.
            normalized = normalize_ip_for_key(reporter_ip)
            recent = NoteReport.objects.filter(
                note=note,
                reporter_ip__isnull=False,
                created_at__gte=timezone.now() - DEDUPE_WINDOW,
            ).values_list('reporter_ip', flat=True)
            if any(normalize_ip_for_key(ip) == normalized for ip in recent):
                raise ValidationError(REPEAT_MESSAGE)

        return NoteReport.objects.create(
            note=note,
            link=fresh,
            reason=reason,
            details=details,
            reporter_email=reporter_email,
            reporter_ip=reporter_ip,
        )


def dismiss_report(report: NoteReport, *, by: User, note: str = '') -> bool:
    """Dismiss one pending report: no action needed on its note. Returns whether this call
    dismissed it (False when it had already been reviewed)."""
    now = timezone.now()
    changed = NoteReport.objects.filter(pk=report.pk, status=ReportStatus.PENDING).update(
        status=ReportStatus.DISMISSED, reviewed_by=by, reviewed_at=now, resolution_note=note
    )
    if not changed:
        return False
    report.status = ReportStatus.DISMISSED
    report.reviewed_by = by
    report.reviewed_at = now
    report.resolution_note = note
    return True
