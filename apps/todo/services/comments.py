"""Comments on a task, with an edit history and soft deletion (issue #180).

Every rule lives here, so the web views (and later the API) enforce the same ones; a refusal is a
`django.core.exceptions.ValidationError` keyed by field (`'__all__'` for none).

An edit never loses text: the body it replaces becomes a `CommentRevision`. A deletion only stamps
`deleted_at` (and empties the search copy and the references): the body and the revisions are kept,
and `restore_comment` brings the comment back. Revisions are never searched.

A comment can carry files (`Attachment.comment`): the browser uploads them first as drafts
(`services.attachments`), and `add_comment` / `edit_comment` take their ids and tie them to the
comment; `edit_comment` also deletes the ones the edit removes. A refusal keyed `attachment_ids`
means a file was not ready or not the user's.
"""

from collections.abc import Iterable

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.accounts.models import User
from apps.core.markdown import search_text

from ..models import Comment, CommentRevision, Task
from .attachments import link_to_comment, remove_from_comment
from .references import sync_references

#: The longest comment, in characters.
COMMENT_MAX = 10_000
#: The most times one comment can be edited.
MAX_REVISIONS = 100

EMPTY_MESSAGE = 'Write a comment.'
DELETED_MESSAGE = 'This comment was deleted.'
TOO_MANY_EDITS_MESSAGE = 'This comment has been edited too many times.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def clean_comment_body(body: object) -> str:
    """The comment text stripped, with `\\r\\n` normalised (a browser submits it, and it would count
    twice against the cap); 1 to `COMMENT_MAX` characters."""
    cleaned = str(body or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not cleaned:
        raise _error('body', EMPTY_MESSAGE)
    if len(cleaned) > COMMENT_MAX:
        raise _error('body', f'Comment is too long (at most {COMMENT_MAX:,} characters).')
    return cleaned


def _body_text(body: str) -> str:
    return search_text(body)  # `COMMENT_MAX` is far below the Markdown size guard


def add_comment(
    task: Task, user: User, body: str, attachment_ids: Iterable[object] = ()
) -> Comment:
    """Add a comment by `user` to their own task. A completed task can be commented on; a task in
    the Trash cannot. Its `#1234` mentions are recorded (`sync_references`), and the uploaded
    drafts in `attachment_ids` become its files (all or nothing: one that isn't ready refuses the
    comment)."""
    if task.user_id != user.pk:
        raise _error(NON_FIELD_ERRORS, 'Unknown task.')
    cleaned = clean_comment_body(body)
    with transaction.atomic():
        # Re-read: the task may have been trashed or deleted since the caller loaded it.
        current = Task.objects.select_for_update().filter(pk=task.pk).first()
        if current is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        if current.is_trashed:
            raise _error(NON_FIELD_ERRORS, 'Restore the task before commenting on it.')
        comment = Comment.objects.create(
            task=task, user=user, body=cleaned, body_text=_body_text(cleaned)
        )
        sync_references(task, comment)
        link_to_comment(comment, attachment_ids)
    return comment


def edit_comment(
    comment: Comment,
    body: str,
    attachment_ids: Iterable[object] = (),
    remove_attachment_ids: Iterable[object] = (),
) -> Comment:
    """Replace the comment's text, keeping the old text as a revision (oldest first in
    `comment_history`). Refused for a deleted comment and after `MAX_REVISIONS` edits; the same
    text makes no revision. The comment is re-read under its row lock, so two edits at once make
    two revisions, never one lost. `attachment_ids` (uploaded drafts) are added to its files and
    `remove_attachment_ids` (files it has) deleted; changing only the files is not an edit of the
    text, so it leaves no revision and no "(edited)"."""
    cleaned = clean_comment_body(body)
    with transaction.atomic():
        fresh = (
            Comment.objects.select_for_update().select_related('task').filter(pk=comment.pk).first()
        )
        if fresh is None:
            raise _error(NON_FIELD_ERRORS, 'This comment no longer exists.')
        if fresh.is_deleted:
            raise _error(NON_FIELD_ERRORS, DELETED_MESSAGE)
        link_to_comment(fresh, attachment_ids)
        if cleaned != fresh.body:
            if fresh.revision_count >= MAX_REVISIONS:
                raise _error(NON_FIELD_ERRORS, TOO_MANY_EDITS_MESSAGE)
            now = timezone.now()
            CommentRevision.objects.create(comment=fresh, body=fresh.body, replaced_at=now)
            fresh.body = cleaned
            fresh.body_text = _body_text(cleaned)
            fresh.edited_at = now
            fresh.revision_count += 1
            fresh.save(update_fields=['body', 'body_text', 'edited_at', 'revision_count'])
            sync_references(fresh.task, fresh)
    # Outside the transaction: a storage call must not hold the comment's lock.
    _copy_state(fresh, comment)
    remove_from_comment(fresh, remove_attachment_ids)
    return comment


def delete_comment(comment: Comment) -> Comment:
    """Soft-delete: `deleted_at` is set, the search copy is emptied (a deleted comment is never
    found) and its references are removed. The body and the revisions stay. A deleted comment is
    left as it is."""
    with transaction.atomic():
        fresh = Comment.objects.select_for_update().filter(pk=comment.pk).first()
        if fresh is None:
            raise _error(NON_FIELD_ERRORS, 'This comment no longer exists.')
        if not fresh.is_deleted:
            fresh.deleted_at = timezone.now()
            fresh.body_text = ''
            fresh.save(update_fields=['deleted_at', 'body_text'])
            fresh.task_references.all().delete()
    _copy_state(fresh, comment)
    return comment


def restore_comment(comment: Comment) -> Comment:
    """Undo `delete_comment`: the search copy and the references are rebuilt from the body. A
    comment that is not deleted is left as it is."""
    with transaction.atomic():
        fresh = (
            Comment.objects.select_for_update().select_related('task').filter(pk=comment.pk).first()
        )
        if fresh is None:
            raise _error(NON_FIELD_ERRORS, 'This comment no longer exists.')
        if fresh.is_deleted:
            fresh.deleted_at = None
            fresh.body_text = _body_text(fresh.body)
            fresh.save(update_fields=['deleted_at', 'body_text'])
            sync_references(fresh.task, fresh)
    _copy_state(fresh, comment)
    return comment


def _copy_state(source: Comment, target: Comment) -> None:
    """Bring the caller's instance up to date with the row the service changed (or found)."""
    if source is target:
        return
    for name in ('body', 'body_text', 'edited_at', 'deleted_at', 'revision_count'):
        setattr(target, name, getattr(source, name))


def comment_history(comment: Comment) -> list[CommentRevision]:
    """The earlier texts of the comment, oldest first (each with when it was replaced)."""
    return list(CommentRevision.objects.filter(comment=comment).order_by('replaced_at', 'pk'))


def with_comment_counts(queryset: QuerySet) -> QuerySet:
    """Annotate `comment_count` (comments that are not deleted) on a queryset of tasks. A subquery,
    not a join: a list already counts subtasks with one, and two joins would multiply the counts."""
    counts = (
        Comment.objects.filter(task=OuterRef('pk'), deleted_at__isnull=True)
        .order_by()
        .values('task')
        .annotate(total=Count('pk'))
        .values('total')
    )
    return queryset.annotate(comment_count=Coalesce(Subquery(counts), 0))
