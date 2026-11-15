"""The owner's "new files" notice for a file request (issue #306), in-app only and **debounced**.

Every upload that finishes (by anyone but the owner) pushes `Transfer.upload_notice_due_at` back
`request_upload.NOTICE_DEBOUNCE` (15 minutes) and, if the batch has not started, sets
`upload_notice_since`. The scheduler job `file_transfer.send_request_notices` (every minute) calls
`send_due_notices`: for each open request whose notice is due it claims the batch with one
conditional `UPDATE` (clearing `upload_notice_due_at`, so a concurrent or repeated run sends
nothing), counts the files uploaded since `upload_notice_since` (the owner's own uploads are not counted), resets that column and, when the
count is above 0, writes one notification -- `3 new files in "Wedding photos"`, naming who they
are from -- with the key `transfer_request_uploads:<id>`, replacing the previous one (`delete_for_key`
then `notify`), so the bell has one, the latest.

Files that were removed meanwhile are not counted (a batch whose files were all removed sends
nothing); a request marked done is skipped (the Done notice folds this one in, see
`services.requests.mark_done`); an owner who cannot sign in is not told. No email.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.core.services import notifications

from ..models import Transfer, TransferFile, TransferKind
from .requests import owner_can_be_told, requests_dashboard_url, uploads_notice_key

logger = logging.getLogger(__name__)

#: Longest a notification title may be (`apps.core.services.notifications`).
_TITLE_MAX = 140

#: How many uploader names the notice body lists before "and N other(s)".
_NAMES_SHOWN = 2


def _title(count: int, request_title: str) -> str:
    noun = 'file' if count == 1 else 'files'
    prefix = f'{count} new {noun} in "'
    room = _TITLE_MAX - len(prefix) - 1
    shown = (
        request_title if len(request_title) <= room else request_title[: room - 1].rstrip() + '…'
    )
    return f'{prefix}{shown}"'


def _uploader_names(files: list[TransferFile]) -> str:
    """ "From Jane, john@example.com and 1 other": the uploaders of the batch, named when known
    (the typed or account name, else the account email); an anonymous uploader who gave no name
    only counts toward "others"."""
    seen: dict[str, str] = {}
    for file in files:
        key = file.uploader_key
        if not key:
            continue
        label = file.uploader_name or file.uploader_email
        if key not in seen or (label and not seen[key]):
            seen[key] = label
    named = [label for label in seen.values() if label]
    shown = named[:_NAMES_SHOWN]
    if not shown:
        return ''
    others = len(seen) - len(shown)
    if others > 0:
        joined = ', '.join(shown)
        return f'From {joined} and {others} other{"s" if others != 1 else ""}'
    return 'From ' + ' and '.join(shown) if len(shown) == 2 else f'From {shown[0]}'


def send_due_notices(*, now=None) -> int:
    """Send every notice that is due; returns how many notifications were written. Idempotent: the
    claim makes a second run (or a concurrent one) find nothing."""
    now = now or timezone.now()
    due = Transfer.objects.filter(
        kind=TransferKind.REQUEST, upload_notice_due_at__lte=now, done_at__isnull=True
    ).values_list('pk', flat=True)
    sent = 0
    for pk in list(due):
        try:
            if _send_one(pk, now):
                sent += 1
        except Exception:
            logger.exception('Could not send the new-files notice of request %s', pk)
    return sent


def _send_one(pk: object, now) -> bool:
    with transaction.atomic():
        claimed = Transfer.objects.filter(
            pk=pk, upload_notice_due_at__lte=now, done_at__isnull=True
        ).update(upload_notice_due_at=None)
        if not claimed:
            return False
        transfer = Transfer.objects.select_related('owner').get(pk=pk)
        since = transfer.upload_notice_since
        Transfer.objects.filter(pk=pk).update(upload_notice_since=None)
        if since is None or not owner_can_be_told(transfer.owner):
            return False
        files = list(
            transfer.files.filter(uploaded=True, uploaded_at__gte=since).exclude(
                uploader_user_id=transfer.owner_id
            )
        )
        if not files:
            return False
        key = uploads_notice_key(transfer.pk)
        notifications.delete_for_key(transfer.owner_id, key, source='file_transfer')
        notifications.notify(
            transfer.owner_id,
            source='file_transfer',
            title=_title(len(files), transfer.title),
            body=_uploader_names(files),
            url=requests_dashboard_url(),
            key=key,
        )
        return True
