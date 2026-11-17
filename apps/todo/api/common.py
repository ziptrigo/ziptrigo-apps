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
- **402 / 409 / 404 for exports** (issue #326, as Receipts' export API): `POST /exports` while the
  account is frozen or the balance can't pay is a 402; while another export is being prepared (one
  at a time) or with an `expected_cost` that is no longer the cost (the detail names the real
  total; nothing was charged or created) a 409, and `DELETE /exports/{id}` of one still being
  prepared a 409; another user's export, a deleted one and a part number the export doesn't have
  are a 404. Files in no task of the range ("nothing to export") are a 400.
- **422** django-ninja's own validation, before an endpoint runs, with a *list-valued* `detail`:
  a malformed id or number in the path, a mistyped query parameter or body field, an unknown key
  in a body (`extra='forbid'`).
- **429** the site-wide rate limiter (`ratelimit.enforce`), with `Retry-After`. The rules and so
  the budgets are the web's (`TODO_CREATE_USER`, `TODO_EDIT_USER`, `TODO_COMMENT_USER`,
  `TODO_SEARCH_USER`, `TODO_PARSE_USER`, `TODO_UPLOAD_USER`, `TODO_UPLOAD_COMPLETE_USER`,
  `TODO_DOWNLOAD_USER`, `TODO_REGENERATE_USER`, and for exports `TODO_EXPORT_FILES_USER`,
  `TODO_EXPORT_DIALOG_USER`, `TODO_EXPORT_DOWNLOAD_USER` and `TODO_EXPORT_XLSX_USER`).
"""

import re
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
from apps.core.date_ranges import DATE_RANGE_ANY, DATE_RANGE_CUSTOM, DATE_RANGES
from apps.core.datetime_format import PUBLIC
from apps.core.markdown import ReferenceResolver
from apps.core.services.export_errors import (
    ExportCostChangedError,
    ExportInProgressError,
    ExportPartMissingError,
)

from ..models import (
    Attachment,
    ExportStatus,
    Reminder,
    Resolution,
    Task,
    TaskBlocker,
    TodoAccount,
    TodoExport,
)
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
    with_open_occurrences,
    with_subtask_counts,
)
from ..services.export_range import ExportRange
from ..services.exports import part_name

#: The columns of a linked task that `task_ref` reads.
LINK_FIELDS = ('number', 'title', 'completed_at', 'deleted_at')

#: Page size bounds of the list endpoints (clamped, not rejected).
DEFAULT_LIMIT = 50
MAX_LIMIT = 100

#: The only date format an export takes (see `_strict_date`).
_STRICT_DATE = re.compile(r'\d{4}-\d{2}-\d{2}', re.ASCII)


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
def mapped_errors(*, keep: type[ValidationError] | None = None) -> Generator[None]:
    """Turn the services' exceptions into the API's statuses (400 / 402, see the module docs).
    `keep` is a `ValidationError` subclass the caller answers itself (the 409 of `POST /tasks`):
    it passes through untouched."""
    try:
        yield
    except AccountFrozenError as exc:
        raise HttpError(402, exc.messages[0]) from exc
    except InsufficientCreditsError as exc:
        raise HttpError(402, str(exc) or 'Insufficient credits') from exc
    except ExportCostChangedError as exc:
        # Before the generic `ValidationError`, which the export errors all subclass.
        raise HttpError(
            409,
            f'The cost changed: it is now {exc.quote} credits. '
            f'Send expected_cost={exc.quote} to confirm it.',
        ) from exc
    except ExportInProgressError as exc:
        raise HttpError(409, exc.messages[0]) from exc
    except ExportPartMissingError as exc:
        raise HttpError(404, exc.messages[0]) from exc
    except ValidationError as exc:
        if keep is not None and isinstance(exc, keep):
            raise
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


def _strict_date(name: str, value: str | None) -> date | None:
    """`parse_date` for an export, which takes only `YYYY-MM-DD`: `date.fromisoformat` also reads
    `20261009` and the ISO week date `2026-W41-5`, which would quietly mean another day than the
    documented format (the task list's filters keep the lenient `parse_date`)."""
    if value is not None and value.strip() and not _STRICT_DATE.fullmatch(value.strip()):
        raise HttpError(400, f'{name} must be a date like 2026-10-09.')
    return parse_date(name, value)


def export_range(date_range: str | None, date_from: str | None, date_to: str | None) -> ExportRange:
    """The "Task created" range of an export request, validated (a 400 for anything wrong, before
    any rate limit is charged). Stricter than the account page's form, which quietly drops what it
    can't read: a client that pays for an export must get the range it asked for, so an unknown
    `date_range`, a malformed date, dates sent with a preset (or with `any`) and a first date after
    the last are all refused. No `date_range` is `custom` when a date is sent, else `any`."""
    first = _strict_date('date_from', date_from)
    last = _strict_date('date_to', date_to)
    key = (date_range or '').strip()
    if key:
        key = choice('date_range', key, (value for value, _label in DATE_RANGES))
    else:
        key = DATE_RANGE_CUSTOM if first or last else DATE_RANGE_ANY
    if key != DATE_RANGE_CUSTOM:
        if first or last:
            raise HttpError(
                400, f'date_from and date_to go with date_range=custom, not with {key}.'
            )
        return ExportRange(date_range=key)
    if first and last and first > last:
        raise HttpError(400, 'date_from must not be after date_to.')
    return ExportRange(date_range=key, date_from=first, date_to=last)


# -- Lookups -------------------------------------------------------------------------------------


def owned_export(request, export_id: UUID) -> TodoExport:
    """The caller's export (not a deleted one), or a 404, never a 403. Its owner is joined (the
    part names are dated in their time zone) and its parts are prefetched."""
    return get_object_or_404(
        TodoExport.objects.exclude(status=ExportStatus.DELETED)
        .select_related('user')
        .prefetch_related('parts'),
        pk=export_id,
        user=request.auth,
    )


def export_data(export: TodoExport) -> dict:
    """An export as the response schema reads it. The parts come from a prefetch (`parts` is
    ordered by number), so a list of exports costs one query for all of them."""
    stored = export.filters or {}
    return {
        'id': export.pk,
        'status': export.status,
        'filters': {'summary': stored.get('summary', []), 'params': stored.get('params', {})},
        'file_count': export.file_count,
        'skipped_count': export.skipped_count,
        'total_bytes': export.total_bytes,
        'part_count': export.part_count,
        'price_per_file': export.price_per_file,
        'charged_credits': export.charged_credits,
        'refunded_credits': export.refunded_credits,
        'parts': [
            {
                'number': part.number,
                'filename': part_name(export, part.number),
                'size_bytes': part.size_bytes,
                'file_count': part.file_count,
                'built': part.built_at is not None,
            }
            for part in export.parts.all()
        ],
        'created_at': export.created_at,
        'ready_at': export.ready_at,
        'expires_at': export.expires_at,
    }


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


def linked_tasks(side: str) -> QuerySet:
    """The `TaskBlocker` rows with the task on `side` (`blocker` or `blocked`) loaded for `task_ref`
    only (number, title, closed, trashed): not the description, a page of 100 tasks with 20 links
    each would load 2,000 of them whole."""
    return TaskBlocker.objects.select_related(side).only(
        'id', 'blocker', 'blocked', *(f'{side}__{name}' for name in LINK_FIELDS)
    )


def decorated(queryset: QuerySet) -> QuerySet:
    """What a task's JSON needs, loaded without a query per task: the subtask, comment and
    attachment counts, `open_occurrences` (one more subquery column), the parent, the repeat rule,
    the project and the section (joined: no query of their own),
    the labels, the reminders and the tasks it is linked to (`blocked_by` and `blocks`: one query
    each for the whole page)."""
    reminders = Reminder.objects.order_by('-minutes_before', 'created_at')
    blocked_by = linked_tasks('blocker').order_by('blocker__number')
    blocks = linked_tasks('blocked').order_by('blocked__number')
    return (
        with_open_occurrences(
            with_attachment_counts(with_comment_counts(with_subtask_counts(queryset)))
        )
        .select_related('parent', 'recurrence', 'project', 'section')
        .prefetch_related(
            'labels',
            Prefetch('reminders', reminders, to_attr='api_reminders'),
            Prefetch('blocked_by_links', blocked_by, to_attr='api_blocked_by'),
            Prefetch('blocks_links', blocks, to_attr='api_blocks'),
        )
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
        'snoozed_until': reminder.snoozed_until,
        'skipped': reminder.skipped,
    }


def task_ref(other: Task) -> dict[str, Any]:
    """A linked task as `TaskRefSchema`: open means not closed and not in the Trash."""
    return {
        'number': other.number,
        'title': other.title,
        'open': other.is_open,
        'trashed': other.is_trashed,
    }


def resolution_of(task: Task) -> str | None:
    """How a closed task was closed (`done` also for one with none recorded), `None` while open."""
    if task.completed_at is None:
        return None
    return Resolution.WONT_DO.value if task.is_wont_do else Resolution.DONE.value


def task_data(task: Task, context: TaskContext) -> dict[str, Any]:
    """A task (loaded by `decorated`) as the `TaskSchema` dict."""
    task.user = context.user  # already loaded: no query per task for the zone
    active = task.recurrence
    reminders = getattr(task, 'api_reminders', None)
    if reminders is None:
        reminders = list(task.reminders.order_by('-minutes_before', 'created_at'))
    blocked_by = getattr(task, 'api_blocked_by', None)
    if blocked_by is None:
        blocked_by = list(linked_tasks('blocker').filter(blocked=task).order_by('blocker__number'))
    blocks = getattr(task, 'api_blocks', None)
    if blocks is None:
        blocks = list(linked_tasks('blocked').filter(blocker=task).order_by('blocked__number'))
    blocked_by_refs = [task_ref(link.blocker) for link in blocked_by]
    # The web's `Task.offers_stop_repeating`, which also reads the `open_occurrences` annotation;
    # a trashed task is never offered it (its own count would not include it).
    offers_stop = task.deleted_at is None and task.offers_stop_repeating
    open_occurrences = getattr(task, 'open_occurrences', None)  # set by `decorated`
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
        'closed_at': task.completed_at,
        'state': task.state if task.completed_at is None else None,
        'resolution': resolution_of(task),
        'deleted_at': task.deleted_at,
        'position': task.position,
        'source': task.source,
        'email_from': task.email_from if task.is_from_email else None,
        'email_from_authenticated': task.email_from_authenticated if task.is_from_email else None,
        'parent': task.parent.number if task.parent is not None else None,
        'labels': [
            {'id': label.pk, 'name': label.name, 'color': label.color}
            for label in task.labels.all()
        ],
        'project': None
        if task.project is None
        else {
            'id': task.project.pk,
            'name': task.project.name,
            'color': task.project.color,
            'archived': task.project.is_archived,
        },
        'section': None
        if task.section is None
        else {'id': task.section.pk, 'name': task.section.name},
        'project_position': task.project_position,
        'recurrence': None
        if active is None
        else {
            'rrule': active.rrule,
            'rrule_text': recurrence.describe(active.rrule, PUBLIC),
            'mode': active.mode,
            'anchor_date': active.anchor_date,
            'ended_at': active.ended_at,
            'open_occurrences': open_occurrences,
        },
        'offers_stop_repeating': offers_stop,
        'reminders': [reminder_data(reminder) for reminder in reminders],
        'blocked_by': blocked_by_refs,
        'has_open_blocker': any(ref['open'] for ref in blocked_by_refs),
        'blocks': [task_ref(link.blocked) for link in blocks],
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


def comment_attachment_data(attachment: Attachment) -> dict[str, Any]:
    return {
        'id': attachment.pk,
        'filename': attachment.filename,
        'size_bytes': attachment.size_bytes,
        'inline_type': attachment.inline_type,
        'inline': attachment.is_inline,
    }


def search_fields(task: Task) -> dict[str, Any]:
    """The `TaskListItemSchema` search fields of a task of a searched list, after
    `services.search.attach_matches` has read it: where it matched and the snippet as plain text
    with the match's offsets; `search_field` is `''` and the rest `None` when there is no snippet.
    (A list that was not searched leaves all four `None`: don't call this for it.)"""
    parts = getattr(task, 'search_parts', None)
    field = getattr(task, 'search_field', '')
    if parts is None or not field:
        return {
            'search_field': '',
            'search_snippet': None,
            'match_start': None,
            'match_end': None,
        }
    before, hit, after = parts
    return {
        'search_field': field,
        'search_snippet': before + hit + after,
        'match_start': len(before),
        'match_end': len(before) + len(hit),
    }


def task_detail(request, task: Task) -> dict[str, Any]:
    """`task` with its uploaded attachments and the tasks that mention it (`TaskDetailSchema`)."""
    data = task_response(request, task)
    uploaded = Attachment.objects.filter(task=task, uploaded=True, for_comment=False).order_by(
        'created_at', 'id'
    )
    data['attachments'] = [attachment_data(item, task.number) for item in uploaded]
    data['files_toward_limit'] = Attachment.objects.filter(task=task).count()
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
