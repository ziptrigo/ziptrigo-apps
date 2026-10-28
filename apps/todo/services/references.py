"""Task references: `#1234` and task URLs in descriptions and comments (issues #178, #180).

`apps.core.markdown` finds the numbers a text mentions and asks a resolver for their URLs; this is
the To-do app's: only the user's own tasks that are not in the Trash link anywhere (anything else
stays text). The same rule decides what is *stored*: `sync_references` keeps a `TaskReference` row
for every task a description or a comment mentions, which is what a task page's "Referenced by"
list (`referenced_by`) reads.
"""

import logging
import re

from django.db import transaction
from django.db.models import F
from django.template.defaultfilters import linebreaksbr
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.accounts.models import User
from apps.core.markdown import ReferenceResolver, extract_references, render

from ..models import Comment, Task, TaskReference

logger = logging.getLogger(__name__)

#: What a task page's path looks like (`todo:task`), for links written as URLs.
TASK_URL_PATH = re.compile(r'^/todo/(?P<number>[0-9]{1,9})/?$')


def resolver_for(user: User) -> ReferenceResolver:
    """A resolver mapping task numbers to `reverse('todo:task', args=[number])` for the user's
    non-trashed tasks among them (one query per call, and none for a number it has already been
    asked about: render several texts through one resolver to look each task up once)."""
    known: dict[int, str | None] = {}

    def resolve(numbers: list[int]) -> dict[int, str]:
        unknown = [number for number in numbers if number not in known]
        if unknown:
            found = set(
                Task.objects.filter(
                    user=user, number__in=unknown, deleted_at__isnull=True
                ).values_list('number', flat=True)
            )
            for number in unknown:
                known[number] = reverse('todo:task', args=[number]) if number in found else None
        return {number: url for number in numbers if (url := known[number]) is not None}

    return resolve


def render_markdown(text: str, resolver: ReferenceResolver) -> SafeString:
    """`text` as sanitised HTML with `#1234` and task URLs linked through `resolver`. A text over
    the Markdown size guard (the services cap far below it) is shown escaped instead of failing the
    page."""
    try:
        return render(text, reference_resolver=resolver, task_url_path=TASK_URL_PATH)
    except ValueError:
        logger.warning('A text of %d characters is shown unrendered', len(text))
        return format_html('<p>{}</p>', linebreaksbr(text, autoescape=True))


def extract_task_numbers(markdown: str) -> list[int]:
    """The numbers of the tasks `markdown` mentions: `#1234` and links to a task page
    (`/todo/<n>/`, or the same on the host of `settings.BASE_URL`), in order, once each. A text over
    the Markdown size guard mentions nothing."""
    try:
        return extract_references(markdown, task_url_path=TASK_URL_PATH)
    except ValueError:
        return []


def sync_references(task: Task, comment: Comment | None = None) -> None:
    """Make the `TaskReference` rows of `(task, comment)` equal what the text mentions.

    The text is `task.description` (`comment` is None) or `comment.body`. A number counts when it
    is one of the task's owner's non-trashed tasks, open or completed; the task itself does not (a
    self-reference is not stored). Missing rows are inserted and stale ones deleted in one
    transaction. Called when a task is created, when its description changes and whenever a
    comment is added, edited, deleted or restored (a deleted comment has no references).
    """
    text = task.description if comment is None else comment.body
    numbers = extract_task_numbers(text)
    with transaction.atomic():
        wanted: set = set()
        if numbers:
            wanted = set(
                Task.objects.filter(
                    user_id=task.user_id, number__in=numbers, deleted_at__isnull=True
                )
                .exclude(pk=task.pk)
                .values_list('pk', flat=True)
            )
        rows = TaskReference.objects.filter(from_task=task, comment=comment)
        existing = set(rows.values_list('to_task_id', flat=True))
        if existing - wanted:
            rows.filter(to_task_id__in=existing - wanted).delete()
        if wanted - existing:
            # `ignore_conflicts`: a racing sync of the same text inserts the same rows.
            TaskReference.objects.bulk_create(
                [
                    TaskReference(from_task=task, to_task_id=pk, comment=comment)
                    for pk in wanted - existing
                ],
                ignore_conflicts=True,
            )


def referenced_by(task: Task) -> list[tuple[Task, Comment | None]]:
    """The tasks that mention `task`, open and completed (not the Trash), each with the comment the
    mention is in (None: its description), ordered by the referencing task's number, then the
    description before the comments, oldest first. A task that mentions it several times in
    different places appears once per place."""
    rows = (
        TaskReference.objects.filter(
            to_task=task, from_task__deleted_at__isnull=True, comment__deleted_at__isnull=True
        )
        .select_related('from_task', 'comment')
        .order_by('from_task__number', F('comment__created_at').asc(nulls_first=True), 'pk')
    )
    return [(row.from_task, row.comment) for row in rows]
