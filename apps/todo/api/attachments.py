"""The attachment endpoints (`/api/todo/tasks/{number}/attachments`, `/api/todo/attachments/{id}`):
the API/CLI twin of the task page's upload script (`apps.todo.views.attachments`), sharing its
rate limits.

1. `POST /tasks/{number}/attachments` registers the file and returns a presigned `PUT` URL.
2. The client `PUT`s the bytes to it with the returned headers.
3. `POST /tasks/{number}/attachments/{id}/complete` verifies the object (size and SHA-256) and
   counts it: only then is it attached, and billed as storage.

Downloading is a short-lived presigned URL (`GET /attachments/{id}/url`), never a redirect and
never the S3 key. While the account's storage is paused (unpaid) uploads and downloads are a 402.
"""

import logging
from uuid import UUID

from django.http import Http404
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from ..models import Attachment
from ..schemas import AttachmentSchema, FileUrlSchema, UploadStartSchema, UploadTicketSchema
from ..services import attachments
from ..services.storage import GET_URL_EXPIRES_SECONDS
from .common import attachment_data, charge, choice, mapped_errors, owned_task
from .router import router

logger = logging.getLogger(__name__)


def _owned_attachment(request, attachment_id: UUID) -> Attachment:
    """The caller's uploaded attachment, or a 404 (one that never completed isn't one yet)."""
    return get_object_or_404(
        Attachment.objects.select_related('task', 'user'),
        pk=attachment_id,
        user=request.auth,
        uploaded=True,
    )


@router.post('/tasks/{number}/attachments', response={201: UploadTicketSchema})
def start_upload(request, number: int, payload: UploadStartSchema):
    """Start attaching one file to an open task. 402 while storage is paused; 400 for an empty or
    too large file (`max_attachment_bytes`, 50 MB by default), a task that already has its
    `max_attachments_per_task` (20), a malformed checksum, or a task in the Trash or completed."""
    charge(request, 'TODO_UPLOAD_USER')
    task = owned_task(request, number)
    with mapped_errors():
        ticket = attachments.start_upload(
            task,
            filename=payload.filename,
            size=payload.size,
            declared_type=payload.content_type,
            checksum_sha256=payload.checksum_sha256,
        )
    return 201, {
        'attachment_id': ticket.attachment.pk,
        'url': ticket.url,
        'headers': ticket.headers,
        'expires_in': ticket.expires_in,
    }


@router.post('/tasks/{number}/attachments/{attachment_id}/complete', response=AttachmentSchema)
def complete_upload(request, number: int, attachment_id: UUID):
    """Finish an upload after the `PUT`. Idempotent. 400 if the object isn't there yet or can't be
    read back (try again), or if it was rejected (wrong size or checksum: the upload is discarded);
    404 if the upload no longer exists."""
    charge(request, 'TODO_UPLOAD_COMPLETE_USER')
    try:
        task = owned_task(request, number)
        attachment = get_object_or_404(Attachment, pk=attachment_id, task=task, user=request.auth)
        with mapped_errors():
            attachment = attachments.complete_upload(attachment)
    except Http404, Attachment.DoesNotExist:
        # The row (or its task) is gone: a PUT that landed afterwards must not stay behind.
        attachments.discard_orphan_object(request.auth.pk, attachment_id)
        raise HttpError(404, 'This upload no longer exists.') from None
    return attachment_data(attachment, task.number)


@router.get('/attachments/{attachment_id}/url', response=FileUrlSchema)
def attachment_url(request, attachment_id: UUID, disposition: str = 'attachment'):
    """A short-lived presigned URL for the file. `disposition=attachment` (the default) downloads
    it; `inline` is for showing it in a browser and is honoured only for a PDF or an image (by what
    the bytes are, never the name), otherwise the answer says `attachment`. 402 while storage is
    paused. Never cache the URL; ask again."""
    charge(request, 'TODO_DOWNLOAD_USER')
    choice('disposition', disposition, ('attachment', 'inline'))
    attachment = _owned_attachment(request, attachment_id)
    wants_inline = disposition == 'inline'
    try:
        with mapped_errors():
            url = attachments.download_url(attachment, inline=wants_inline)
    except HttpError as exc:
        if exc.status_code == 400:  # not downloadable (never completed): as the web, a 404
            raise HttpError(404, 'Not Found') from exc
        raise
    return {
        'url': url,
        'disposition': 'inline' if wants_inline and attachment.is_inline else 'attachment',
        'filename': attachment.filename,
        'expires_in': GET_URL_EXPIRES_SECONDS,
    }


@router.delete('/attachments/{attachment_id}', response={204: None})
def delete_attachment(request, attachment_id: UUID):
    """Remove a file from its task: the object and the row, and its bytes stop being billed. Also
    allowed for a task in the Trash and while storage is paused (it saves money)."""
    charge(request, 'TODO_EDIT_USER')
    attachment = _owned_attachment(request, attachment_id)
    try:
        attachments.delete_attachment(attachment)
    except attachments.STORAGE_ERRORS as exc:
        logger.exception('Could not delete attachment %s', attachment.pk)
        raise HttpError(503, 'The file could not be removed right now. Try again later.') from exc
    return 204, None
