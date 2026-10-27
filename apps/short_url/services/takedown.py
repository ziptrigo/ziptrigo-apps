"""Admin takedown of a link (issue #162): a distinct terminal status (`TAKEN_DOWN`, not a reuse
of `DELETED`, so the dashboard, the API and any reporting can tell staff's removal from the
owner's own delete). Reached only from the admin (`apps.short_url.admin`), never an action an
owner can trigger. Like a delete, the row stays, so the code is never issued again.
"""

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services.queue import enqueue_after_commit

from ..models import LinkStatus, ReportStatus, ShortLink
from .emails import send_link_taken_down_email


def take_down_link(
    link: ShortLink,
    *,
    by: User,
    reason: str,
    internal_note: str = '',
    notify: bool = False,
) -> bool:
    """Take `link` down: it stops working for good, who did it and why is recorded, any
    abuse-review hold is cleared (moot once terminal) and every pending report against it is
    marked actioned, all in one transaction. The owner is *not* told by default: `notify=True`
    queues "one of your links was removed" (`emails.send_link_taken_down_email`, to a confirmed
    address only), once the transaction commits.

    `reason` is staff-authored and always shown to the owner (the link's page and the email) --
    never who reported it. `internal_note` is staff-only and shown nowhere else.

    A conditional write: it only moves an `active` or `disabled` link. Returns whether *this call*
    took it down; a link the owner deleted (or staff already took down) since `link` was read is
    left alone and nothing is recorded.
    """
    now = timezone.now()
    with transaction.atomic():
        changed = ShortLink.objects.filter(
            pk=link.pk, status__in=(LinkStatus.ACTIVE, LinkStatus.DISABLED)
        ).update(
            status=LinkStatus.TAKEN_DOWN,
            taken_down_by=by,
            taken_down_at=now,
            takedown_reason=reason,
            takedown_internal_note=internal_note,
            held_for_review_at=None,
            updated_at=now,
        )
        if not changed:
            link.refresh_from_db()
            return False
        link.reports.filter(status=ReportStatus.PENDING).update(
            status=ReportStatus.ACTIONED,
            reviewed_by=by,
            reviewed_at=now,
            resolution_note='Link taken down.',
        )
        if notify:
            enqueue_after_commit(send_link_taken_down_email, str(link.pk))
    link.status = LinkStatus.TAKEN_DOWN
    link.taken_down_by = by
    link.taken_down_at = now
    link.takedown_reason = reason
    link.takedown_internal_note = internal_note
    link.held_for_review_at = None
    link.updated_at = now
    return True
