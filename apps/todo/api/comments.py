"""The comment endpoints (`/api/todo/tasks/{number}/comments`): add, edit, delete (soft), restore
and read the edit history. Each calls the service the task page uses.

A comment is addressed by its id under its task's number; a comment of another task is a 404, and
so is another user's task. Adding and editing spend `TODO_COMMENT_USER`; deleting and restoring
spend `TODO_EDIT_USER` (and nothing when the comment is already in the state asked for).
"""

from uuid import UUID

from django.shortcuts import get_object_or_404

from .. import services
from ..models import Comment, Task
from ..schemas import CommentBodySchema, CommentSchema, RevisionSchema
from .common import charge, context_for, mapped_errors, owned_task
from .router import router


def _owned_comment(task: Task, comment_id: UUID) -> Comment:
    """The comment, if it is one of `task`'s; any other is a 404."""
    return get_object_or_404(Comment, pk=comment_id, task=task)


def _data(request, task: Task, comment: Comment, *, bodies: bool = True) -> dict:
    """A comment as `CommentSchema` shows it; a deleted one has no body unless `bodies`."""
    show = bodies or not comment.is_deleted
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
    }


@router.get('/tasks/{number}/comments', response=list[CommentSchema])
def list_comments(request, number: int, include_deleted_bodies: bool = False):
    """The task's comments, oldest first. A deleted comment is listed with `deleted_at` and no
    body (`null`) unless `include_deleted_bodies=1` asks for its text too. A task in the Trash
    still shows its comments."""
    task = owned_task(request, number)
    comments = Comment.objects.filter(task=task)
    return [_data(request, task, comment, bodies=include_deleted_bodies) for comment in comments]


@router.post('/tasks/{number}/comments', response={201: CommentSchema})
def add_comment(request, number: int, payload: CommentBodySchema):
    """Add a Markdown comment (1 to 10,000 characters). `#1234` and task URLs in it link to tasks,
    and the tasks mentioned list this one under "referenced by". A task in the Trash takes none
    (400)."""
    task = owned_task(request, number)
    with mapped_errors():
        services.comments.clean_comment_body(payload.body)  # a refusal here spends no budget
        charge(request, 'TODO_COMMENT_USER')
        comment = services.add_comment(task, request.auth, payload.body)
    return 201, _data(request, task, comment)


@router.patch('/tasks/{number}/comments/{comment_id}', response=CommentSchema)
def edit_comment(request, number: int, comment_id: UUID, payload: CommentBodySchema):
    """Replace a comment's text. The text it replaces is kept in its history
    (`.../history`); the same text changes nothing; a deleted comment cannot be edited (400)."""
    task = owned_task(request, number)
    comment = _owned_comment(task, comment_id)
    with mapped_errors():
        services.comments.clean_comment_body(payload.body)  # a refusal here spends no budget
        charge(request, 'TODO_COMMENT_USER')
        services.edit_comment(comment, payload.body)
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
