"""Admin takedown of a note (issue #260, PR 4): final, and reached only from the admin
(`apps.notes.admin`), never an action an owner can trigger. There is no release.

A taken-down note is read-only for its owner (who sees the reason, never the internal note nor
who reported it), every share link of it stops working, it can't be shared or copied, it can still
be trashed and deleted, and it keeps renewing like any note until then. The owner is not emailed.
Modelled on Short URLs' takedown (`apps.short_url.services.takedown`).
"""

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User

from ..models import Note, NoteReport, ReportStatus


def take_down_note(note: Note, *, by: User, reason: str, internal_note: str = '') -> bool:
    """Take `note` down: who did it, when and why are recorded and every pending report against it
    is marked actioned, all in one transaction.

    `reason` is staff-authored and always shown to the owner on the note's page -- never who
    reported it. `internal_note` is staff-only and shown nowhere else.

    A conditional write (`taken_down_at IS NULL`). Returns whether *this call* took it down; a note
    that was already taken down (or deleted since `note` was read) is left alone and nothing is
    recorded. Takes no other lock than the note's own row, so it never waits behind the owner's
    transactions (which lock the account first) for long and can't deadlock with them.
    """
    now = timezone.now()
    with transaction.atomic():
        changed = Note.objects.filter(pk=note.pk, taken_down_at__isnull=True).update(
            taken_down_at=now,
            taken_down_by=by,
            takedown_reason=reason,
            takedown_internal_note=internal_note,
        )
        if not changed:
            note.refresh_from_db()
            return False
        NoteReport.objects.filter(note_id=note.pk, status=ReportStatus.PENDING).update(
            status=ReportStatus.ACTIONED,
            reviewed_by=by,
            reviewed_at=now,
            resolution_note='Note taken down.',
        )
    note.taken_down_at = now
    note.taken_down_by = by
    note.takedown_reason = reason
    note.takedown_internal_note = internal_note
    return True
