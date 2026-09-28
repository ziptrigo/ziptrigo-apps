"""Admin takedown of a transfer (issue #59): a distinct terminal status (`TAKEN_DOWN`, see
`TransferStatus`'s docstring for why it isn't just `DELETED`), reached only from
`apps.file_transfer.admin` -- never a dashboard or API action a sender can trigger themselves.
"""

from django.utils import timezone

from apps.accounts.models import User

from ..models import AbuseReportStatus, Transfer, TransferStatus
from .lifecycle import end_transfer


def take_down_transfer(
    transfer: Transfer, *, by: User, reason: str, notify: bool = False
) -> Transfer:
    """Take `transfer` down: ends it (deleting its S3 objects, including the "download all" zip,
    same as any other ended transfer -- `services.lifecycle.end_transfer`), records who did it and
    why, clears any abuse-review hold (moot once it's ended), and marks every pending report
    against it as actioned. The sender is *not* notified by default -- `notify=True` sends the
    same "files deleted" email any other file deletion does.
    """
    now = timezone.now()
    Transfer.objects.filter(pk=transfer.pk).update(
        taken_down_by=by, taken_down_at=now, takedown_reason=reason, held_for_review_at=None
    )
    transfer.taken_down_by = by
    transfer.taken_down_at = now
    transfer.takedown_reason = reason
    transfer.held_for_review_at = None

    end_transfer(transfer, TransferStatus.TAKEN_DOWN, delete_files=True, notify=notify)

    transfer.reports.filter(status=AbuseReportStatus.PENDING).update(
        status=AbuseReportStatus.ACTIONED,
        reviewed_by=by,
        reviewed_at=now,
        resolution_note='Transfer taken down.',
    )
    return transfer
