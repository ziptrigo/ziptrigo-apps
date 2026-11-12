"""A task's activity log on the task page (issue #274): `GET /todo/<n>/activity/`.

The log is not rendered with the task: the page has a small "History" button that fetches this
only when it is pressed (`static/todo/js/activity.js`), the latest `PAGE_SIZE` entries, newest first,
and a "Show more" that fetches the next page (`?before=<cursor>`: the time and id of the last entry
shown, so entries written meanwhile can't repeat or skip one). It only reads, so a task in the Trash
works too; another user's task is a 404. **No rate-limit rule**, like the notification endpoints: it
is session-bound, one indexed query for a page of 20 and no more.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.accounts.http import AuthenticatedHttpRequest

from ..models import Task
from ..services import events
from ..services.event_text import describe
from .common import owned_task

PAGE_SIZE = events.PAGE_SIZE


def activity_context(task: Task, cursor: str = '') -> dict:
    """One page of `task`'s log: `entries` (`(event, sentence)`, newest first) and `more`, the
    cursor of the next page (empty when this is the last)."""
    try:
        page, more = events.page(task, limit=PAGE_SIZE, cursor=cursor)
    except ValueError:  # a malformed cursor
        raise Http404('Unknown page') from None
    return {
        'task': task,
        'entries': [(event, describe(event)) for event in page],
        'more': more,
        'first_page': not cursor,
    }


@login_required
@require_GET
def task_activity(request: AuthenticatedHttpRequest, number: int) -> HttpResponse:
    """The first page of the log (the section: heading, list, "Show more"), or, with `before`, the
    next page's rows with the "Show more" slot swapped out of band."""
    task = owned_task(request, number, trashed=None)
    cursor = request.GET.get('before', '')
    template = 'todo/partials/activity_rows.html' if cursor else 'todo/partials/activity.html'
    return render(request, template, activity_context(task, cursor))
