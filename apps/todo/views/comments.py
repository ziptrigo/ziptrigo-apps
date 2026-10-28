"""Comments on a task (issue #180): add, edit, delete, restore, the edit history and a deleted
comment's text.

The comments section (`todo/partials/comments.html`, `#task-comments`) sits beside the task
page's panel (a panel action doesn't touch it, so a draft survives) and is what each action
answers, so only it is re-rendered. The rules are
`services.comments`'; a refusal is a 422 with `Label: message` lines swapped into the slot of the
control that was used. Another user's task, a comment of another task and a trashed task (for
the actions that change anything) are a 404.
"""

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.htmx import is_htmx

from ..forms import CommentForm
from ..models import Comment, Task
from ..services import (
    add_comment,
    comment_history,
    delete_comment,
    edit_comment,
    render_markdown,
    resolver_for_request,
    restore_comment,
)
from .common import form_error_lines, limited, owned_task, refuse, validation_lines

ADD_MSG = '#comment-msg'


def _task_url(task: Task, comment: Comment | None = None) -> str:
    url = reverse('todo:task', args=[task.number])
    return f'{url}#comment-{comment.pk}' if comment is not None else f'{url}#task-comments'


def _owned_comment(task: Task, comment_id: UUID) -> Comment:
    """The comment, if it is one of `task`'s; any other is a 404."""
    return get_object_or_404(Comment, pk=comment_id, task=task)


def comments_context(request: AuthenticatedHttpRequest, task: Task) -> dict:
    """What `comments.html` needs: the task's comments, each with its rendered `html` (empty for a
    deleted one, whose text is fetched on request), rendered through one resolver so each task a
    comment mentions is looked up once."""
    resolver = resolver_for_request(request)
    comments = list(Comment.objects.filter(task=task).select_related('user'))
    for comment in comments:
        comment.html = '' if comment.is_deleted else render_markdown(comment.body, resolver)
    return {
        'task': task,
        'comments': comments,
        'comment_total': sum(1 for comment in comments if not comment.is_deleted),
    }


def comments_response(
    request: AuthenticatedHttpRequest, task: Task, *, status: int = 200
) -> HttpResponse:
    return render(
        request, 'todo/partials/comments.html', comments_context(request, task), status=status
    )


def _done(
    request: AuthenticatedHttpRequest, task: Task, comment: Comment | None = None
) -> HttpResponse:
    """The answer to a successful action: the comments section (htmx), else back to the page."""
    if is_htmx(request):
        return comments_response(request, task)
    return redirect(_task_url(task, comment))


def _refuse(
    request: AuthenticatedHttpRequest,
    task: Task,
    lines: list[str],
    *,
    slot: str = ADD_MSG,
) -> HttpResponse:
    return refuse(request, lines, retarget=slot, fallback=_task_url(task))


@login_required
@require_POST
def comment_add(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    task = owned_task(request, number)
    form = CommentForm(request.POST)
    if not form.is_valid():
        return _refuse(request, task, form_error_lines(form))
    if (response := limited(request, 'TODO_COMMENT_USER', retarget=ADD_MSG)) is not None:
        return response
    try:
        comment = add_comment(task, request.user, form.cleaned_data['body'])
    except ValidationError as exc:
        return _refuse(request, task, validation_lines(exc))
    return _done(request, task, comment)


@login_required
@require_POST
def comment_edit(request: AuthenticatedHttpRequest, number: int, comment_id: UUID) -> HttpResponse:
    task = owned_task(request, number)
    comment = _owned_comment(task, comment_id)
    slot = f'#comment-msg-{comment.pk}'
    form = CommentForm(request.POST)
    if not form.is_valid():
        return _refuse(request, task, form_error_lines(form), slot=slot)
    if (response := limited(request, 'TODO_COMMENT_USER', retarget=slot)) is not None:
        return response
    try:
        edit_comment(comment, form.cleaned_data['body'])
    except ValidationError as exc:
        return _refuse(request, task, validation_lines(exc), slot=slot)
    return _done(request, task, comment)


@login_required
@require_POST
def comment_delete(
    request: AuthenticatedHttpRequest, number: int, comment_id: UUID
) -> HttpResponse:
    task = owned_task(request, number)
    comment = _owned_comment(task, comment_id)
    if comment.is_deleted:  # already there: nothing to do, so nothing to charge
        return _done(request, task, comment)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=ADD_MSG)) is not None:
        return response
    try:
        delete_comment(comment)
    except ValidationError as exc:
        return _refuse(request, task, validation_lines(exc))
    if not is_htmx(request):
        messages.success(request, 'Comment deleted.')
    return _done(request, task, comment)


@login_required
@require_POST
def comment_restore(
    request: AuthenticatedHttpRequest, number: int, comment_id: UUID
) -> HttpResponse:
    task = owned_task(request, number)
    comment = _owned_comment(task, comment_id)
    if not comment.is_deleted:  # already there: nothing to do, so nothing to charge
        return _done(request, task, comment)
    if (response := limited(request, 'TODO_EDIT_USER', retarget=ADD_MSG)) is not None:
        return response
    try:
        restore_comment(comment)
    except ValidationError as exc:
        return _refuse(request, task, validation_lines(exc))
    if not is_htmx(request):
        messages.success(request, 'Comment restored.')
    return _done(request, task, comment)


@login_required
@require_GET
def comment_history_view(
    request: AuthenticatedHttpRequest, number: int, comment_id: UUID
) -> HttpResponse:
    """The earlier texts of a comment, newest first, each rendered, with when it was replaced.
    Works for a deleted comment and a task in the Trash too (it only reads)."""
    task = owned_task(request, number, trashed=None)
    comment = _owned_comment(task, comment_id)
    resolver = resolver_for_request(request)
    revisions = comment_history(comment)[::-1]
    for revision in revisions:
        revision.html = render_markdown(revision.body, resolver)
    return render(
        request,
        'todo/partials/comment_history.html',
        {'task': task, 'comment': comment, 'revisions': revisions},
    )


@login_required
@require_GET
def comment_deleted_view(
    request: AuthenticatedHttpRequest, number: int, comment_id: UUID
) -> HttpResponse:
    """The original text of a deleted comment, rendered, with Restore. A comment that is not
    deleted is a 404."""
    task = owned_task(request, number, trashed=None)
    comment = _owned_comment(task, comment_id)
    if not comment.is_deleted:
        raise Http404('Comment not found')
    comment.html = render_markdown(comment.body, resolver_for_request(request))
    return render(request, 'todo/partials/comment_deleted.html', {'task': task, 'comment': comment})
