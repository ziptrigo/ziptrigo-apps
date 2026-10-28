"""What the To-do API endpoints share: ownership lookups, the mapping from service errors to HTTP
statuses, rate limits, and how a task is turned into its JSON shape.

Status codes (documented once, here):

- **400** a `ValidationError` from a service (`field: message`, or the message alone for one that
  belongs to no field), or one of the few query parameters read by hand (`view`, `mode`, `sort`,
  `priority`, `label`, `date_range`, `date_from`/`date_to`, `disposition`). A reference in a body
  to something that isn't yours (a label, a `parent`) is a 400 too, with the service's own
  message (`labels: Unknown label.`): the thing being changed exists, it is the value that is
  refused.
- **402** `AccountFrozenError` (attachment storage unpaid: uploads and downloads are refused until
  the balance is topped up) and `InsufficientCreditsError` (the balance can't cover a new task).
  One code for both, as in the Receipts API.
- **404** a task, label, comment, reminder, attachment or saved filter that isn't the caller's
  (never 403, so a number or an id can't be probed), and a request the web answers with a 404 too
  (deleting a task forever that isn't in the Trash).
- **409** `POST /tasks` while a repeating task's next occurrence is waiting to be paid for:
  `{detail, unpaid_task_numbers}`.
- **422** django-ninja's own validation, before an endpoint runs, with a *list-valued* `detail`:
  a malformed id or number in the path, a mistyped query parameter or body field, an unknown key
  in a body (`extra='forbid'`).
- **429** the site-wide rate limiter (`ratelimit.enforce`), with `Retry-After`. The rules and so
  the budgets are the web's (`TODO_CREATE_USER`, `TODO_EDIT_USER`, `TODO_COMMENT_USER`,
  `TODO_SEARCH_USER`, `TODO_PARSE_USER`, `TODO_UPLOAD_USER`, `TODO_UPLOAD_COMPLETE_USER`,
  `TODO_DOWNLOAD_USER`, `TODO_REGENERATE_USER`).
"""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Prefetch, QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.accounts.models import User
from apps.billing.services import InsufficientCreditsError
from apps.core import ratelimit
from apps.core.markdown import ReferenceResolver

from ..models import Attachment, Reminder, Task, TodoAccount
from ..services import (
    AccountFrozenError,
    dates,
    get_account,
    recurrence,
    referenced_by,
    render_markdown,
    resolver_for,
    with_attachment_counts,
    with_comment_counts,
    with_subtask_counts,
)

#: Page size bounds of the list endpoints (clamped, not rejected).
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def validation_detail(exc: ValidationError) -> str:
    """One readable line for a `ValidationError`: the first message, or `field: message` pairs when
    the service keyed its errors by field (a message that belongs to no field is plain)."""
    if hasattr(exc, 'error_dict'):
        return ' '.join(
            message if field == '__all__' else f'{field}: {message}'
            for field, messages in exc.message_dict.items()
            for message in messages
        )
    return exc.messages[0]


@contextmanager
def mapped_errors() -> Generator[None]:
    """Turn the services' exceptions into the API's statuses (400 / 402, see the module docs)."""
    try:
        yield
    except AccountFrozenError as exc:
        raise HttpError(402, exc.messages[0]) from exc
    except InsufficientCreditsError as exc:
        raise HttpError(402, str(exc) or 'Insufficient credits') from exc
    except ValidationError as exc:
        raise HttpError(400, validation_detail(exc)) from exc


def clamp_page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_LIMIT)), max(0, offset)


def charge(request, rule: str) -> None:
    """Spend one hit of the user's `rule` budget: a 429 when it is over."""
    ratelimit.enforce(ratelimit.hit_user(request.auth, rule))


def choice(name: str, value: str, allowed: Iterable[str]) -> str:
    """`value` if it is one of `allowed`, else a 400 naming the choices."""
    options = tuple(allowed)
    if value not in options:
        raise HttpError(400, f'{name} must be one of: {", ".join(options)}.')
    return value


def parse_date(name: str, value: str | None) -> date | None:
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HttpError(400, f'{name} must be a date like 2026-10-09.') from None


# -- Lookups -------------------------------------------------------------------------------------


def owned_task(request, number: int, *, trashed: bool | None = None) -> Task:
    """The caller's task numbered `number`, or a 404. `trashed=None` (the default) accepts a task
    in the Trash too -- the service the endpoint calls says what is allowed on it --, `False`
    refuses one, `True` accepts only one."""
    queryset = Task.objects.select_related('user', 'parent', 'recurrence')
    task = get_object_or_404(queryset, user=request.auth, number=number)
    if trashed is not None and task.is_trashed != trashed:
        raise Http404('Task not found')
    return task


def task_from_body(user: User, number: int | None) -> Task | None:
    """The task a body names by number (`parent`): `None` for no task. One that isn't the
    caller's, or doesn't exist, is refused as the services refuse a parent they can't use."""
    if number is None:
        return None
    task = Task.objects.filter(user=user, number=number).select_related('parent').first()
    if task is None:
        raise ValidationError({'parent': 'Unknown parent task.'})
    return task


# -- Serialising a task --------------------------------------------------------------------------


@dataclass(slots=True)
class TaskContext:
    """What turning tasks into JSON needs, made once per request: the owner, their account (the
    default due time) and the resolver that links `#1234` in a description."""

    user: User
    account: TodoAccount
    resolver: ReferenceResolver


def context_for(request) -> TaskContext:
    context = getattr(request, '_todo_task_context', None)
    if context is None:
        user = request.auth
        context = TaskContext(user=user, account=get_account(user), resolver=resolver_for(user))
        request._todo_task_context = context
    return context


def decorated(queryset: QuerySet) -> QuerySet:
    """What a task's JSON needs, loaded without a query per task: the subtask, comment and
    attachment counts, the parent, the repeat rule, the labels and the reminders."""
    reminders = Reminder.objects.order_by('-minutes_before', 'created_at')
    return (
        with_attachment_counts(with_comment_counts(with_subtask_counts(queryset)))
        .select_related('parent', 'recurrence')
        .prefetch_related('labels', Prefetch('reminders', reminders, to_attr='api_reminders'))
    )


def load_task(request, task: Task) -> Task:
    """`task` again, loaded the way a list loads its rows (fresh after an action changed it)."""
    return decorated(Task.objects.filter(user=request.auth, pk=task.pk)).get()


def reminder_data(reminder: Reminder) -> dict[str, Any]:
    return {
        'id': reminder.pk,
        'minutes_before': reminder.minutes_before,
        'channel': reminder.channel,
        'fire_at': reminder.fire_at,
        'sent_at': reminder.sent_at,
    }


def task_data(task: Task, context: TaskContext) -> dict[str, Any]:
    """A task (loaded by `decorated`) as the `TaskSchema` dict."""
    task.user = context.user  # already loaded: no query per task for the zone
    active = task.recurrence
    reminders = getattr(task, 'api_reminders', None)
    if reminders is None:
        reminders = list(task.reminders.order_by('-minutes_before', 'created_at'))
    return {
        'id': task.pk,
        'number': task.number,
        'title': task.title,
        'description': task.description,
        'description_html': str(render_markdown(task.description, context.resolver))
        if task.description
        else '',
        'priority': task.priority,
        'due_date': task.due_date,
        'due_time': task.due_time,
        'due_at': dates.due_at(task, context.account),
        'completed_at': task.completed_at,
        'deleted_at': task.deleted_at,
        'position': task.position,
        'source': task.source,
        'parent': task.parent.number if task.parent is not None else None,
        'labels': [
            {'id': label.pk, 'name': label.name, 'color': label.color}
            for label in task.labels.all()
        ],
        'recurrence': None
        if active is None
        else {
            'rrule': active.rrule,
            'rrule_text': recurrence.describe(active.rrule),
            'mode': active.mode,
            'anchor_date': active.anchor_date,
            'ended_at': active.ended_at,
        },
        'reminders': [reminder_data(reminder) for reminder in reminders],
        'subtask_count': task.subtask_total,
        'open_subtask_count': task.subtask_open,
        'comment_count': task.comment_count,
        'attachment_count': task.attachment_count,
        'charged_credits': task.charged_credits,
        'unpaid': task.is_unpaid,
        'created_at': task.created_at,
        'updated_at': task.updated_at,
    }


def task_response(request, task: Task) -> dict[str, Any]:
    """`task`, reloaded, as its JSON dict (what every endpoint that changes a task answers)."""
    return task_data(load_task(request, task), context_for(request))


def attachment_data(attachment: Attachment, number: int) -> dict[str, Any]:
    return {
        'id': attachment.pk,
        'task': number,
        'filename': attachment.filename,
        'size_bytes': attachment.size_bytes,
        'declared_type': attachment.declared_type,
        'inline_type': attachment.inline_type,
        'created_at': attachment.created_at,
    }


def task_detail(request, task: Task) -> dict[str, Any]:
    """`task` with its uploaded attachments and the tasks that mention it (`TaskDetailSchema`)."""
    data = task_response(request, task)
    uploaded = Attachment.objects.filter(task=task, uploaded=True).order_by('created_at', 'id')
    data['attachments'] = [attachment_data(item, task.number) for item in uploaded]
    data['referenced_by'] = [
        {'number': source.number, 'title': source.title, 'comment': comment.pk if comment else None}
        for source, comment in referenced_by(task)
    ]
    return data


def unpaid_task_numbers(user: User) -> list[int]:
    """The numbers of the user's tasks whose price is still owed, oldest first."""
    return list(
        Task.objects.filter(user=user, unpaid_at__isnull=False)
        .order_by('unpaid_at', 'number')
        .values_list('number', flat=True)
    )


def uuid_or_none(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None
