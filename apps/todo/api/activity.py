"""A task's activity log (`GET /api/todo/tasks/{number}/activity`): every change made to it,
newest first, with a sentence for each. Reads the same entries and sentences the task page's
"History" shows (`services.events`, `services.event_text`).

Spends no rate limit, like the web page: it is JWT-bound and one indexed query for a page of at
most 100 entries.
"""

from ninja.errors import HttpError

from apps.core.datetime_format import PUBLIC, override

from ..schemas import ActivityPageSchema
from ..services import events
from ..services.event_text import describe
from .common import clamp_page, owned_task
from .router import router

DEFAULT_PAGE = events.PAGE_SIZE


@router.get('/tasks/{number}/activity', response=ActivityPageSchema)
def task_activity(request, number: int, limit: int = DEFAULT_PAGE, before: str | None = None):
    """The task's activity log, **newest first**, also for a task in the Trash (another user's task
    is a 404).

    `limit` (1-100, default 20) is the page size. `next` in the answer is the cursor of the next,
    older page: send it as `before` to continue (`null` on the last page); the cursor is the time
    and id of the last entry, so entries written while paging never repeat or skip one. A malformed
    `before` is a 400. Each entry's `text` is a sentence in the public wording (English, dates like
    `Nov 5, 2026, 3:00 PM`, whatever the account's date and time format); see `ActivityEventSchema`
    for `kind` and `data`.
    """
    task = owned_task(request, number)
    limit, _ = clamp_page(limit, 0)
    try:
        page, more = events.page(task, limit=limit, cursor=before or '')
    except ValueError:
        raise HttpError(400, 'before: Not a valid cursor.') from None
    with override(PUBLIC):  # the API's text is the public wording, whatever was last active
        results = [
            {
                'id': event.pk,
                'kind': event.kind,
                'text': describe(event),
                'data': event.data,
                'automatic': event.automatic,
                'channel': event.channel,
                'created_at': event.created_at,
            }
            for event in page
        ]
    return {'results': results, 'next': more or None}
