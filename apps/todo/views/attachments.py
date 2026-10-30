"""Attachments on the task page (issue #184).

The browser sends each file straight to S3 with a presigned PUT and only coordinates with Django
over JSON (so, like Receipts' upload views, `start` and `complete` answer JSON rather than HTMX
partials):

1. `POST /todo/<n>/attachments/start/` with the file's name, size, type and base64 SHA-256 -> a
   presigned URL plus the headers the signature binds (`services.attachments.start_upload`).
2. The browser PUTs the file to that URL.
3. `POST /todo/<n>/attachments/<id>/complete/` -> the object is verified and counted
   (`services.attachments.complete_upload`).

A pending upload the user cancels is removed by `POST /todo/<n>/attachments/<id>/cancel/`
(`cancel_upload`, `TODO_EDIT_USER`), so it stops counting toward the task's cap; it is idempotent
(an upload that is already gone is `ok`) and refuses a file that is already attached.

Statuses of the JSON endpoints: 422 for a refused file (size, the cap, the task's state, the
checksum, a task in the trash) *and* for a frozen account (with `frozen` and a `top_up_url`, which the script shows as a
"Top up" link: htmx is not involved, so there is no 402 to avoid), 429 from `TODO_UPLOAD_USER`
(start) or `TODO_UPLOAD_COMPLETE_USER` (complete), 404 for another user's task or attachment, and
for an upload whose row is gone (the task was deleted, or the upload cancelled or abandoned,
meanwhile): `complete` then also deletes any object the browser's PUT left behind.

Downloading redirects to a short-lived presigned URL (`?inline=1` for "Open", honoured only for a
sniffed image or PDF); removing is a plain POST from the shared confirmation modal, redirecting
back with a flash message.
"""

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core import ratelimit
from apps.core.htmx import hx_redirect, is_htmx
from apps.core.json_numbers import whole_number

from ..models import Attachment, Task, TodoSettings
from ..services import attachments, get_account
from ..services.errors import AccountFrozenError
from .common import credits_url, limited, owned_task

logger = logging.getLogger(__name__)


def attachments_context(request: AuthenticatedHttpRequest, task: Task) -> dict:
    """What `todo/partials/attachments.html` needs: the task's uploaded attachments, and whether
    this task takes new ones."""
    config = TodoSettings.load()
    account = get_account(request.user)
    return {
        'task': task,
        'attachments': list(
            Attachment.objects.filter(task=task, uploaded=True, for_comment=False).order_by(
                'created_at', 'id'
            )
        ),
        'attachments_can_upload': not task.is_trashed and not task.is_completed,
        'attachments_frozen': account.is_frozen,
        'attachments_max_bytes': config.max_attachment_bytes,
        'attachments_max_files': config.max_attachments_per_task,
    }


def _json_body(request: HttpRequest) -> dict:
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError, UnicodeDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _owned_attachment(request: AuthenticatedHttpRequest, attachment_id: object) -> Attachment:
    return get_object_or_404(
        Attachment.objects.select_related('task', 'user'),
        pk=attachment_id,
        user=request.user,
        uploaded=True,
    )


def _frozen_response(exc: AccountFrozenError) -> JsonResponse:
    return JsonResponse(
        {'error': exc.messages[0], 'frozen': True, 'top_up_url': credits_url()}, status=422
    )


@login_required
@require_POST
def attachment_start(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    limit = ratelimit.hit_user(request.user, 'TODO_UPLOAD_USER')
    if not limit.allowed:
        return ratelimit.json_response(limit)

    task = owned_task(request, number)
    body = _json_body(request)
    name = str(body.get('name', ''))
    size = whole_number(body.get('size', 0))
    if size is None or size < 0:
        return JsonResponse({'error': 'The file size must be a whole number of bytes.'}, status=422)

    try:
        ticket = attachments.start_upload(
            task,
            filename=name,
            size=size,
            declared_type=str(body.get('content_type', '')),
            checksum_sha256=str(body.get('checksum_sha256', '')),
            for_comment=body.get('for_comment') is True,
        )
    except AccountFrozenError as exc:
        return _frozen_response(exc)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)

    return JsonResponse(
        {
            'attachment_id': str(ticket.attachment.pk),
            'url': ticket.url,
            'headers': ticket.headers,
            'expires_in': ticket.expires_in,
        },
        status=201,
    )


@login_required
@require_POST
def attachment_complete(
    request: AuthenticatedHttpRequest, number: int, attachment_id: str
) -> HttpResponse:
    # Each call is an S3 HEAD, and an attachment whose object never arrived stays `uploaded=False`,
    # so `complete` is not bounded by `start`: it has its own (looser) per-user rule, charged first.
    limit = ratelimit.hit_user(request.user, 'TODO_UPLOAD_COMPLETE_USER')
    if not limit.allowed:
        return ratelimit.json_response(limit)

    try:
        task = owned_task(request, number, trashed=None)
        attachment = get_object_or_404(Attachment, pk=attachment_id, task=task, user=request.user)
        attachment = attachments.complete_upload(attachment)
    except Http404, Attachment.DoesNotExist:
        # The row (or its task) is gone: a PUT that landed afterwards must not stay behind.
        attachments.discard_orphan_object(request.user.pk, attachment_id)
        return JsonResponse({'error': 'This upload no longer exists.'}, status=404)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)
    return JsonResponse(
        {
            'ok': True,
            'attachment_id': str(attachment.pk),
            'filename': attachment.filename,
            'size_bytes': attachment.size_bytes,
        }
    )


@login_required
@require_POST
def attachment_cancel(
    request: AuthenticatedHttpRequest, number: int, attachment_id: str
) -> HttpResponse:
    """Drop a pending upload (the upload script's Cancel button): its row and its object, if the
    PUT landed. Answers `ok` for one that is already gone; a completed file is a 422 (remove it
    instead)."""
    limit = ratelimit.hit_user(request.user, 'TODO_EDIT_USER')
    if not limit.allowed:
        return ratelimit.json_response(limit)
    task = owned_task(request, number, trashed=None)
    attachment = Attachment.objects.filter(pk=attachment_id, task=task, user=request.user).first()
    if attachment is None:
        attachments.discard_orphan_object(request.user.pk, attachment_id)
        return JsonResponse({'ok': True})
    try:
        attachments.cancel_upload(attachment)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def attachment_discard(
    request: AuthenticatedHttpRequest, number: int, attachment_id: str
) -> HttpResponse:
    """Delete a comment file that was uploaded but not saved with a comment yet (the user removed
    it from the comment form). Idempotent like `cancel`; a file that is attached is a 422."""
    limit = ratelimit.hit_user(request.user, 'TODO_EDIT_USER')
    if not limit.allowed:
        return ratelimit.json_response(limit)
    task = owned_task(request, number, trashed=None)
    attachment = Attachment.objects.filter(pk=attachment_id, task=task, user=request.user).first()
    if attachment is None:
        return JsonResponse({'ok': True})
    try:
        attachments.discard_draft(attachment)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=422)
    except attachments.STORAGE_ERRORS:
        logger.exception('Could not delete the unused comment file %s', attachment.pk)
        return JsonResponse({'error': 'The file could not be removed right now.'}, status=503)
    return JsonResponse({'ok': True})


@login_required
@require_GET
def attachment_list(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """The task's attachment list (what the upload script refreshes after a file lands). Opened
    directly rather than by htmx it goes to the task page."""
    task = owned_task(request, number, trashed=None)
    if not is_htmx(request):
        return redirect(reverse('todo:task', args=[task.number]))
    return render(request, 'todo/partials/attachment_list.html', attachments_context(request, task))


@login_required
@require_GET
def attachment_download(request: AuthenticatedHttpRequest, attachment_id: str) -> HttpResponse:
    """Redirect to a fresh presigned URL (`?inline=1` to show it in the browser). While storage is
    frozen: back to the task, with a message."""
    if (response := limited(request, 'TODO_DOWNLOAD_USER')) is not None:
        return response
    attachment = _owned_attachment(request, attachment_id)
    task_url = reverse('todo:task', args=[attachment.task.number])
    try:
        url = attachments.download_url(attachment, inline=request.GET.get('inline') == '1')
    except AccountFrozenError as exc:
        messages.error(request, exc.messages[0])
        return redirect(task_url)
    except ValidationError as exc:
        raise Http404(exc.messages[0]) from exc
    response = redirect(url)
    response['Cache-Control'] = 'no-store, private'
    return response


@login_required
@require_POST
def attachment_delete(request: AuthenticatedHttpRequest, attachment_id: str) -> HttpResponse:
    """Remove a file from its task: the object and the row, and its bytes stop being billed."""
    if (response := limited(request, 'TODO_EDIT_USER')) is not None:
        return response
    attachment = _owned_attachment(request, attachment_id)
    task_url = reverse('todo:task', args=[attachment.task.number])
    try:
        attachments.delete_attachment(attachment)
    except attachments.STORAGE_ERRORS:
        logger.exception('Could not delete attachment %s', attachment.pk)
        messages.error(request, 'The file could not be removed right now. Try again later.')
    else:
        messages.success(request, f'Removed {attachment.filename}.')
    return hx_redirect(request, task_url)
