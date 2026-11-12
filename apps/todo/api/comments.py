"""The comment endpoints (`/api/todo/tasks/{number}/comments`): add, edit, delete (soft), restore
and read the edit history. Each calls the service the task page uses.

A comment is addressed by its id under its task's number; a comment of another task is a 404, and
so is another user's task. Adding and editing spend `TODO_COMMENT_USER`; deleting and restoring
spend `TODO_EDIT_USER` (and nothing when the comment is already in the state asked for).
"""

from uuid import UUID

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404

from .. import services
from ..models import Attachment, Comment, Task
from ..schemas import CommentBodySchema, CommentEditSchema, CommentSchema, RevisionSchema
from ..services import attachments
from .common import charge, comment_attachment_data, context_for, mapped_errors, owned_task
from .router import router


def _owned_comment(task: Task, comment_id: UUID) -> Comment:
    """The comment, if it is one of `task`'s; any other is a 404."""
    return get_object_or_404(Comment, pk=comment_id, task=task)


def _files(comments: list[Comment]) -> dict[object, list[Attachment]]:
    """The uploaded files of `comments`, by comment id, oldest first: one query for all of them."""
    files: dict[object, list[Attachment]] = {}
    ids = [comment.pk for comment in comments]
    if not ids:
        return files
    for attachment in Attachment.objects.filter(comment__in=ids, uploaded=True).order_by(
        'created_at', 'id'
    ):
        files.setdefault(attachment.comment_id, []).append(attachment)
    return files


def _data(
    request,
    task: Task,
    comment: Comment,
    *,
    bodies: bool = True,
    files: dict[object, list[Attachment]] | None = None,
) -> dict:
    """A comment as `CommentSchema` shows it; a deleted one has no body (and no files) unless
    `bodies`. `files` is what `_files` read for a list; for one comment it is read here."""
    show = bodies or not comment.is_deleted
    if files is None:
        files = _files([comment])
    return {
        'id': comment.pk,
        'task': task.number,
        'body': comment.body if show else None,
        'body_html': str(services.render_markdown(comment.body, context_for(request).resolver))
        if show
        else None,
        'created_at': comment.created_at,
        'edited_at': comment.edited_at,
        'deleted_at': comment.deleted_at,
        'revision_count': comment.revision_count,
        'attachments': [comment_attachment_data(item) for item in files.get(comment.pk, [])]
        if show
        else [],
    }


@router.get('/tasks/{number}/comments', response=list[CommentSchema])
def list_comments(request, number: int, include_deleted_bodies: bool = False):
    """The task's comments, oldest first. A deleted comment is listed with `deleted_at` and no
    body (`null`) unless `include_deleted_bodies=1` asks for its text too. A task in the Trash
    still shows its comments."""
    task = owned_task(request, number)
    comments = list(Comment.objects.filter(task=task))
    files = _files(comments)
    return [
        _data(request, task, comment, bodies=include_deleted_bodies, files=files)
        for comment in comments
    ]


@router.post('/tasks/{number}/comments', response={201: CommentSchema})
def add_comment(request, number: int, payload: CommentBodySchema):
    """Add a Markdown comment (1 to 10,000 characters). `#1234` and task URLs in it link to tasks,
    and the tasks mentioned list this one under "referenced by". A task in the Trash takes none
    (400). `attachment_ids` attach comment files uploaded before (see `UploadStartSchema`): all or
    none, a 400 keyed `attachment_ids` if one is not an uploaded, unattached comment file of this
    task. A refused body or file list spends no budget."""
    task = owned_task(request, number)
    with mapped_errors():
        if task.is_trashed:  # refused by the service too, but only after the charge
            raise ValidationError('Restore the task before commenting on it.')
        services.comments.clean_comment_body(payload.body)  # a refusal here spends no budget
        attachments.check_comment_files(task, payload.attachment_ids)
        charge(request, 'TODO_COMMENT_USER')
        comment = services.add_comment(
            task, request.auth, payload.body, attachment_ids=payload.attachment_ids
        )
    return 201, _data(request, task, comment)


@router.patch('/tasks/{number}/comments/{comment_id}', response=CommentSchema)
def edit_comment(request, number: int, comment_id: UUID, payload: CommentEditSchema):
    """Replace a comment's text. The text it replaces is kept in its history
    (`.../history`); the same text changes nothing; a deleted comment cannot be edited (400).
    `attachment_ids` add uploaded comment files (all or none, as when adding) and
    `remove_attachment_ids` delete files of this comment; changing only the files leaves no
    revision. A refused body or file list spends no budget."""
    task = owned_task(request, number)
    comment = _owned_comment(task, comment_id)
    with mapped_errors():
        if comment.is_deleted:  # refused by the service too, but only after the charge
            raise ValidationError(services.comments.DELETED_MESSAGE)
        services.comments.clean_comment_body(payload.body)  # a refusal here spends no budget
        attachments.check_comment_files(task, payload.attachment_ids, comment)
        charge(request, 'TODO_COMMENT_USER')
        services.edit_comment(
            comment,
            payload.body,
            attachment_ids=payload.attachment_ids,
            remove_attachment_ids=payload.remove_attachment_ids,
        )
    return _data(request, task, comment)


@router.delete('/tasks/{number}/comments/{comment_id}', response={204: None})
def delete_comment(request, number: int, comment_id: UUID):
    """Delete a comment. It is only hidden: its text and history stay, and `restore` brings it
    back. Idempotent."""
    task = owned_task(request, number)
    comment = _owned_comment(task, comment_id)
    if not comment.is_deleted:  # already there: nothing to do, so nothing to charge
        charge(request, 'TODO_EDIT_USER')
        with mapped_errors():
            services.delete_comment(comment)
    return 204, None


@router.post('/tasks/{number}/comments/{comment_id}/restore', response=CommentSchema)
def restore_comment(request, number: int, comment_id: UUID):
    """Bring a deleted comment back. Idempotent."""
    task = owned_task(request, number)
    comment = _owned_comment(task, comment_id)
    if comment.is_deleted:
        charge(request, 'TODO_EDIT_USER')
        with mapped_errors():
            services.restore_comment(comment)
    return _data(request, task, comment)


@router.get('/tasks/{number}/comments/{comment_id}/history', response=list[RevisionSchema])
def comment_history(request, number: int, comment_id: UUID):
    """The earlier texts of a comment, oldest first, each with when it was replaced. Works for a
    deleted comment too."""
    task = owned_task(request, number)
    comment = _owned_comment(task, comment_id)
    resolver = context_for(request).resolver
    return [
        {
            'body': revision.body,
            'body_html': str(services.render_markdown(revision.body, resolver)),
            'replaced_at': revision.replaced_at,
        }
        for revision in services.comment_history(comment)
    ]
