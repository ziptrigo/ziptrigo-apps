"""Admin takedown of a transfer (issue #59): a distinct terminal status (`TAKEN_DOWN`, see
`TransferStatus`'s docstring for why it isn't just `DELETED`), reached only from
`apps.file_transfer.admin` -- never a dashboard or API action a sender can trigger themselves.
"""

from django.utils import timezone

from apps.accounts.models import User

from ..models import AbuseReportStatus, Transfer, TransferStatus
from .lifecycle import end_transfer


def take_down_transfer(
    transfer: Transfer,
    *,
    by: User,
    reason: str,
    internal_note: str = '',
    notify: bool = False,
) -> Transfer:
    """Take `transfer` down: ends it (deleting its S3 objects, including the "download all" zip,
    same as any other ended transfer -- `services.lifecycle.end_transfer`), records who did it and
    why, clears any abuse-review hold (moot once it's ended), and marks every pending report
    against it as actioned. The sender is *not* notified by default -- `notify=True` sends a
    "your transfer was removed" email (distinct wording from an ordinary expiry/deletion,
    `services.emails.send_files_deleted_notification`), and the choice is recorded on the
    transfer itself (`takedown_notify`) so a later deferred-deletion sweep
    (`jobs.expire_transfers`, if the S3 delete below fails here and has to be retried) honours it
    instead of defaulting to always-notify.

    `reason` is staff-authored and always shown to the sender (on the dashboard, and in the email
    if notified) -- never the identity of whoever reported it. `internal_note` is staff-only and
    never shown to the sender anywhere.

    Marks the transfer taken down and its pending reports actioned *before* attempting the S3
    delete, in the same transaction as the status change (`end_transfer`'s `before_delete` hook):
    if the delete raises (an S3 outage, say), that's a real error the caller should still see and
    handle, but it must not leave the transfer stuck `ACTIVE` with its reports still `PENDING` --
    `files_deleted_at` simply stays null until a retry (or the deferred-deletion sweep) succeeds,
    same as any other transfer whose file deletion was deferred.
    """
    now = timezone.now()

    def _mark_taken_down() -> None:
        Transfer.objects.filter(pk=transfer.pk).update(
            taken_down_by=by,
            taken_down_at=now,
            takedown_reason=reason,
            takedown_internal_note=internal_note,
            takedown_notify=notify,
            held_for_review_at=None,
        )
        transfer.taken_down_by = by
        transfer.taken_down_at = now
        transfer.takedown_reason = reason
        transfer.takedown_internal_note = internal_note
        transfer.takedown_notify = notify
        transfer.held_for_review_at = None

        transfer.reports.filter(status=AbuseReportStatus.PENDING).update(
            status=AbuseReportStatus.ACTIONED,
            reviewed_by=by,
            reviewed_at=now,
            resolution_note='Transfer taken down.',
        )

    end_transfer(
        transfer,
        TransferStatus.TAKEN_DOWN,
        delete_files=True,
        notify=notify,
        before_delete=_mark_taken_down,
    )
    return transfer
