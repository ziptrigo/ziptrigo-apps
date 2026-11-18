"""Admin takedown of a QR code (issue #5, PR 3): final, and reached only from the admin
(`apps.qr_code.admin`), never an action an owner can trigger. There is no release.

A taken-down code's share links stop working for good, and no link can be made or emailed for it.
The owner still sees, edits, downloads and deletes it, sees the reason (never the internal note nor
who reported it) on the dashboard and in the share dialog, and is not emailed. **A tracked code's
`/go/<code>` redirect is not touched**: it is printed on physical codes, and killing a redirect is a
different decision (`views.redirect` never reads these columns). A copy of Notes' takedown
(`apps.notes.services.takedown`).
"""

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User

from ..models import QRCode, QRShareReport, ReportStatus


def take_down_qrcode(qrcode: QRCode, *, by: User, reason: str, internal_note: str = '') -> bool:
    """Take `qrcode` down: who did it, when and why are recorded and every pending report against
    it is marked actioned, all in one transaction.

    `reason` is staff-authored and always shown to the owner -- never who reported it.
    `internal_note` is staff-only and shown nowhere else.

    A conditional write (`taken_down_at IS NULL`). Returns whether *this call* took it down; a code
    that was already taken down is left alone and nothing is recorded. Takes no other lock than
    the code's own row, so it can't deadlock with the owner's transactions.
    """
    now = timezone.now()
    with transaction.atomic():
        changed = QRCode.objects.filter(pk=qrcode.pk, taken_down_at__isnull=True).update(
            taken_down_at=now,
            taken_down_by=by,
            takedown_reason=reason,
            takedown_internal_note=internal_note,
        )
        if not changed:
            qrcode.refresh_from_db()
            return False
        QRShareReport.objects.filter(qrcode_id=qrcode.pk, status=ReportStatus.PENDING).update(
            status=ReportStatus.ACTIONED,
            reviewed_by=by,
            reviewed_at=now,
            resolution_note='QR code taken down.',
        )
    qrcode.taken_down_at = now
    qrcode.taken_down_by = by
    qrcode.takedown_reason = reason
    qrcode.takedown_internal_note = internal_note
    return True
