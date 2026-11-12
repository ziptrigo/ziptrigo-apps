"""The task endpoints (`/api/todo/tasks`): list and search, create, read, edit, close and reopen,
change the state or the resolution, postpone, trash and restore, reorder, tick a checklist item and
stop repeating. Blockers and the activity log are in `blockers.py` and `activity.py`.

Every endpoint calls the same `apps.todo.services` function the web views do, so the same
validation, billing and ordering rules apply either way. A task is addressed by its **number**
(`#1234`), the user's own. See `common.py` for the status codes.
"""

from contextlib import nullcontext
from typing import Any
from uuid import UUID

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import QuerySet
from ninja.errors import HttpError

from apps.accounts.services.timezones import user_today
from apps.core import ratelimit

from .. import services
from ..limits import MAX_REMINDERS_PER_TASK
from ..models import Reminder, Resolution, Task, TaskBlocker, TaskSource
from ..schemas import (
    ChecklistSchema,
    CloseSchema,
    CountSchema,
    PostponeSchema,
    ReorderSchema,
    ResolutionSchema,
    StateSchema,
    TaskCreatedSchema,
    TaskCreateSchema,
    TaskDetailSchema,
    TaskListSchema,
    TaskSchema,
    TaskUpdateSchema,
    UnpaidBlockSchema,
)
from ..services import (
    SORT_DEFAULT,
    SORTS,
    FilterParams,
    blockers,
    closed_set,
    has_manual_order,
    open_tasks,
    order_tasks,
    order_today,
    reminders,
    subtasks_of,
    today_set,
    todays_tasks,
    trash_set,
    upcoming_set,
)
from ..services.filters import DATE_RANGES, NO_LABEL, PRIORITIES, RESOLUTIONS, STATES
from ..services.search import MODES, order_results, search_guard
from ..services.tasks import NOT_OPEN_MESSAGE
from .common import (
    DEFAULT_LIMIT,
    charge,
    choice,
    clamp_page,
    context_for,
    decorated,
    mapped_errors,
    owned_task,
    parse_date,
    task_data,
    task_detail,
    task_from_body,
    task_response,
    unpaid_task_numbers,
    uuid_or_none,
)
from .parse import read_title
from .router import router

VIEWS = ('today', 'upcoming', 'all', 'closed', 'trash')


# -- List and search -----------------------------------------------------------------------------


def _priorities(value: str | None) -> list[str]:
    tokens = [part.strip() for part in (value or '').split(',') if part.strip()]
    for token in tokens:
        if not token.isdigit() or int(token) not in PRIORITIES:
            raise HttpError(400, 'priority must be a comma-separated list of 0, 1, 2 and 3.')
    return tokens


def _states(value: str | None) -> list[str]:
    tokens = [part.strip().lower() for part in (value or '').split(',') if part.strip()]
    for token in tokens:
        if token not in STATES:
            raise HttpError(400, f'state must be a comma-separated list of: {", ".join(STATES)}.')
    return tokens


def _labels(value: str | None) -> list[str]:
    tokens = [part.strip().lower() for part in (value or '').split(',') if part.strip()]
    for token in tokens:
        if token != NO_LABEL and uuid_or_none(token) is None:
            raise HttpError(400, 'label must be a comma-separated list of label ids and "none".')
    return tokens


def _view_queryset(user, view: str, include_closed: bool, resolution: str = '') -> QuerySet:
    if view == 'today':
        return today_set(user)
    if view == 'upcoming':
        return upcoming_set(user)
    if view == 'closed':
        return closed_set(user, resolution)
    if view == 'trash':
        return trash_set(user)
    if include_closed:
        return Task.objects.filter(user=user, deleted_at__isnull=True)
    return open_tasks(user)


def _ordered(
    queryset: QuerySet, user, *, view: str | None, parent, sort: str | None, include_closed: bool
) -> QuerySet:
    """`queryset` in the order the web shows it: `sort` when asked for, else Today's (overdue
    first, then today's in the order the user gave it), a parent's subtasks (the user's order when
    they gave one), the Closed page's (latest first) and the Trash's, else by date."""
    if view == 'all' and include_closed:
        return order_results(queryset, sort or SORT_DEFAULT)  # closed ones after the open
    if sort is not None:
        return order_tasks(queryset, sort)
    if view == 'today':
        manual = has_manual_order(todays_tasks(user), 'today_position')
        return order_today(queryset, user_today(user), manual=manual)
    if view is None and parent is not None:
        manual = has_manual_order(subtasks_of(parent))
        return order_tasks(queryset, 'manual' if manual else SORT_DEFAULT)
    if view == 'closed':
        return queryset.order_by('-completed_at', '-number')
    if view == 'trash':
        return queryset.order_by('-deleted_at', '-number')
    return order_tasks(queryset)


@router.get('/tasks', response=TaskListSchema)
def list_tasks(
    request,
    view: str | None = None,
    q: str = '',
    mode: str = 'glob',
    include_closed: bool = False,
    resolution: str | None = None,
    state: str | None = None,
    priority: str | None = None,
    label: str | None = None,
    date_range: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str | None = None,
    parent: int | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
):
    """List the caller's tasks, subtasks included.

    - `view`: `today` (open, due today or earlier: the overdue ones, and the On hold and Blocked
      ones due today or earlier too, which are listed but never *overdue*), `upcoming` (open, due in
      the next 14 days), `all` (open; the default), `closed` (closed as Done or as Won't do, latest
      first) or `trash`. (`completed`, this parameter's value before version 2 of the API, is a 400.)
    - `parent`: a task number; lists its subtasks (closed ones too). With a `view`, only those of
      the subtasks that are in the view.
    - `q`: search the titles, descriptions and comments, ignoring case, accents and punctuation:
      `*` and `?` are wildcards (`mode=glob`, the default) or a regular expression
      (`mode=regex`, or `/.../`). 200 characters at most; a 400 for an empty, invalid or too slow
      pattern. Deleted comments and edit history are not searched.
    - `include_closed`: with `view=all`, closed tasks too (after the open ones).
    - `resolution`: `done` or `wont_do`: only the closed tasks closed that way. Allowed with
      `view=closed`, or with `include_closed` (and `view=all`); a 400 otherwise.
    - `state`: comma-separated states, any of `todo`, `in_progress`, `blocked`, `on_hold`; an open
      task in any of them. Closed tasks are not narrowed by it.
    - `priority`: comma-separated priorities, 0 (none) to 3; any of them.
    - `label`: comma-separated label ids, and `none` for tasks without a label; any of them.
    - `date_range`: `any`, `overdue` (open and due before today, **not** On hold or Blocked tasks:
      a muted task is never overdue), `today`, `next_7_days`, `next_30_days`, `no_date` or
      `custom` with `date_from` / `date_to` (`YYYY-MM-DD`, inclusive, either may be left out;
      dates alone mean `custom`). Relative ranges are the caller's own today.
    - `sort`: `date` (the default), `priority`, `created` or `title`.
    - `limit` (1-100, default 50) and `offset` page the result; `count` is the total.

    A search, or any of `state`, `priority`, `label` and `date_range`, spends `TODO_SEARCH_USER`.
    """
    user = request.auth
    limit, offset = clamp_page(limit, offset)
    if view is not None:
        view = choice('view', view, VIEWS)
    mode = choice('mode', mode, MODES)
    if sort is not None:
        choice('sort', sort, SORTS)
    if date_range is not None:
        choice('date_range', date_range, DATE_RANGES)
    parent_task = owned_task(request, parent) if parent is not None else None
    if view is None and parent_task is None:
        view = 'all'
    if resolution is not None:
        choice('resolution', resolution, RESOLUTIONS)
        if view != 'closed' and not (include_closed and view in ('all', None)):
            raise HttpError(
                400,
                'resolution applies only with view=closed, or with include_closed and view=all.',
            )

    params = FilterParams.from_raw(
        {
            'q': [q],
            'mode': [mode],
            'include_completed': ['1' if include_closed or view == 'closed' else ''],
            'resolution': [resolution or ''],
            'states': _states(state),
            'priorities': _priorities(priority),
            'labels': _labels(label),
            'date_range': [date_range or ''],
            'date_from': [str(parse_date('date_from', date_from) or '')],
            'date_to': [str(parse_date('date_to', date_to) or '')],
            'sort': [sort or SORT_DEFAULT],
        }
    )
    searching = params.has_criteria
    if searching:
        charge(request, 'TODO_SEARCH_USER')

    if view is None:
        assert parent_task is not None  # `view` is only unset with a `parent`
        queryset = subtasks_of(parent_task)
    else:
        queryset = _view_queryset(user, view, include_closed, resolution or '')
        if parent_task is not None:
            queryset = queryset.filter(parent=parent_task)
    with mapped_errors():
        if searching or params.resolution:
            queryset = queryset.filter(params.condition(user))  # validates the search text
        with search_guard() if params.q.strip() else nullcontext():
            count = queryset.count()
            rows = list(
                decorated(
                    _ordered(
                        queryset,
                        user,
                        view=view,
                        parent=parent_task,
                        sort=sort,
                        include_closed=include_closed,
                    )
                )[offset : offset + limit]
            )
    context = context_for(request)
    return {
        'count': count,
        'limit': limit,
        'offset': offset,
        'results': [task_data(task, context) for task in rows],
    }


# -- Create --------------------------------------------------------------------------------------


def _apply_reading(
    payload: TaskCreateSchema, reading
) -> tuple[dict[str, Any], str, bool, list[str]]:
    """What the AI's reading of the title changes in the new task: `(values, title, applied,
    kept)`. Each of the date (with its time) and the repeat is used unless the body sets it itself
    (`kept` names what the body kept); the title is cleaned of the words only when everything read
    was used, so no part of what the user wrote vanishes without having been applied."""
    fields = payload.model_fields_set
    values: dict[str, Any] = {}
    kept: list[str] = []
    if 'due_date' in fields or 'due_time' in fields:
        kept.append('the date')
    else:
        values['due_date'], values['due_time'] = reading.due_date, reading.due_time
    if reading.rrule:
        if 'rrule' in fields:
            kept.append('the repeat')
        else:
            values['rrule'] = reading.rrule
    title = payload.title if kept else reading.clean_title
    return values, title, bool(values), kept


def _set_reminders(task, wanted) -> None:
    """Give a new task exactly the reminders asked for, instead of the default one `create_task`
    gave it."""
    for existing in list(Reminder.objects.filter(task=task)):
        reminders.delete_reminder(existing)
    default_channel = services.get_account(task.user).default_reminder_channel
    for item in wanted:
        reminders.add_reminder(
            task, minutes_before=item.minutes_before, channel=item.channel or default_channel
        )


def _check_reminders(user, payload: TaskCreateSchema, *, has_date: bool) -> None:
    """The refusals of the reminders asked for that `reminders.add_reminder` would make one by one
    (a bad time or channel, a repeated time, more than the cap, no due date), told before anything
    is spent. `has_date` is whether the task will have a due date (the reading of the title may
    still give it one: the caller says so)."""
    wanted = payload.reminders
    if not wanted:
        return
    if len(wanted) > MAX_REMINDERS_PER_TASK:
        raise ValidationError({'reminders': f'At most {MAX_REMINDERS_PER_TASK} reminders.'})
    times: set[int] = set()
    for item in wanted:
        minutes = reminders.clean_minutes(item.minutes_before)
        if item.channel:
            reminders.clean_channel(item.channel)
        if minutes in times:
            raise ValidationError({'minutes_before': reminders.DUPLICATE_MESSAGE})
        times.add(minutes)
    if not has_date:
        raise ValidationError({NON_FIELD_ERRORS: reminders.NO_DATE_MESSAGE})


def _check_create(user, payload: TaskCreateSchema, parent: Task | None) -> None:
    """Everything `POST /tasks` can refuse without writing, before the create budget is spent and
    before the title is sent to the model: the fields, a creation blocked by an unpaid repeating
    task (`TaskCreationBlockedError`, the 409), the cap on open tasks and a balance that can't pay
    (402) -- the very checks email intake makes before its own AI call -- and the reminders."""
    due = services.check_new_task(
        user,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        due_date=payload.due_date,
        due_time=payload.due_time,
        parent=parent,
        labels=payload.labels,
        rrule=payload.rrule,
        recurrence_mode=payload.recurrence_mode,
        state=payload.state,
        blocked_by=payload.blocked_by,
    )
    fields = payload.model_fields_set
    # The reading of the title may still set the date, when the body doesn't.
    may_get_date = payload.parse_title and not fields & {'due_date', 'due_time'}
    _check_reminders(user, payload, has_date=due is not None or may_get_date)


def _create(user, payload: TaskCreateSchema, parent: Task | None):
    values: dict[str, Any] = {
        'due_date': payload.due_date,
        'due_time': payload.due_time,
        'rrule': payload.rrule,
    }
    title = payload.title
    parsed: dict[str, Any] | None = None
    if payload.parse_title and title.strip():
        reading, reason = read_title(user, ' '.join(title.split()))
        parsed = {'applied': False, 'label': '', 'matched_text': '', 'reason': reason}
        if reading is not None:
            changed, title, applied, kept = _apply_reading(payload, reading)
            values.update(changed)
            parsed.update(
                applied=applied,
                label=reading.label,
                matched_text=reading.matched_text,
                reason=f'Kept what you set for {" and ".join(kept)}.' if kept else '',
            )
    with transaction.atomic():  # the reminders asked for and the task stand or fall together
        task = services.create_task(
            user,
            title=title,
            description=payload.description,
            priority=payload.priority,
            parent=parent,
            labels=payload.labels,
            recurrence_mode=payload.recurrence_mode,
            state=payload.state,
            blocked_by=payload.blocked_by,
            source=TaskSource.API,
            **values,
        )
        if payload.reminders is not None:
            _set_reminders(task, payload.reminders)
    return task, parsed


@router.post('/tasks', response={201: TaskCreatedSchema, 409: UnpaidBlockSchema})
def create_task(request, payload: TaskCreateSchema):
    """Create a task (or a subtask with `parent`). It counts toward the month's free tasks and then
    costs the task price: a 402 when the balance can't cover it, and a 409
    (`{detail, unpaid_task_numbers}`) while an occurrence of a repeating task is waiting to be paid
    for (a top-up settles it). `source` is `api`.

    See `TaskCreateSchema` for the body, `parse_title`, `reminders`, `state` and `blocked_by` in
    particular; `parsed` in the answer says what `parse_title` did.

    The request is checked first (the fields, the 409, the cap on open tasks, the balance, the
    reminders, the state and the blockers; a title over 500 characters is a 422), and only then are `TODO_CREATE_USER` and, for
    `parse_title`, the date reader's budget spent: a refused request costs neither and never calls
    the model. What only the creating transaction can decide (a race for the last slot or the last
    credits, a repeat rule that fails against the final date) is still refused after the create
    budget was spent.
    """
    user = request.auth
    try:
        with mapped_errors(keep=services.TaskCreationBlockedError):
            parent = task_from_body(user, payload.parent)
            _check_create(user, payload, parent)
            charge(request, 'TODO_CREATE_USER')
            task, parsed = _create(user, payload, parent)
    except services.TaskCreationBlockedError:
        return 409, {
            'detail': services.tasks.BLOCKED_MESSAGE,
            'unpaid_task_numbers': unpaid_task_numbers(user),
        }
    data = task_response(request, task)
    data['parsed'] = parsed
    return 201, data


# -- Many tasks ----------------------------------------------------------------------------------
# Registered before the `{number}` routes: `reorder` is not a task number, but a router tries the
# paths in the order they were added.


@router.post('/tasks/reschedule-overdue', response=CountSchema)
def reschedule_overdue(request):
    """Set the due date of every overdue open task to today, keeping the times. `count` is how many
    changed. A task that is On hold or Blocked is never overdue, so it is neither moved nor
    counted."""
    charge(request, 'TODO_EDIT_USER')
    return {'count': services.reschedule_overdue(request.auth)}


@router.post('/tasks/reorder', response=CountSchema)
def reorder_tasks(request, payload: ReorderSchema):
    """Order the tasks due today (`scope=today`; the overdue ones keep the default order) or the
    subtasks of `parent` (`scope=subtasks`) by hand: the ids in the order wanted. Ids outside the
    set are ignored; `count` is how many were positioned."""
    parent = owned_task(request, payload.parent) if payload.parent is not None else None
    if payload.scope not in services.tasks.REORDER_SCOPES:
        raise HttpError(400, 'scope: Choose what to order.')
    if payload.scope == 'subtasks' and parent is None:
        raise HttpError(400, 'parent: Choose the task whose subtasks to order.')
    charge(request, 'TODO_EDIT_USER')
    ids: list[UUID] = list(payload.ids)
    with mapped_errors():
        count = services.reorder(request.auth, scope=payload.scope, ids=ids, parent=parent)
    return {'count': count}


# -- One task ------------------------------------------------------------------------------------


@router.get('/tasks/{number}', response=TaskDetailSchema)
def get_task(request, number: int):
    """One task, also one in the Trash, with its uploaded attachments and the tasks that mention
    it."""
    return task_detail(request, owned_task(request, number))


def _plan_blockers(task: Task, user, wanted: list[int] | None) -> tuple[list[Task], list[Task]]:
    """`(to_add, to_remove)` to make `wanted` (task numbers; `None` is none) the set of tasks that
    block `task`, with every refusal that can be told without writing (an unknown or trashed task,
    itself, a cycle, more than the cap)."""
    numbers = list(dict.fromkeys(wanted or []))
    if len(numbers) > services.MAX_BLOCKERS_PER_TASK:
        raise ValidationError({'blocked_by': blockers.TOO_MANY_MESSAGE})
    current = {
        link.blocker.number: link.blocker
        for link in TaskBlocker.objects.filter(blocked=task).select_related('blocker')
    }
    to_add = blockers.tasks_by_numbers(user, [n for n in numbers if n not in current])
    to_remove = [other for number, other in current.items() if number not in numbers]
    if task.is_trashed and (to_add or to_remove):
        raise ValidationError('Restore the task before changing it.')
    for other in to_add:
        blockers.check_add(task, other)
    return to_add, to_remove


@router.patch('/tasks/{number}', response=TaskSchema)
def update_task(request, number: int, payload: TaskUpdateSchema):
    """Edit a task. Only the fields present in the body are applied, all or none (a refusal
    changes nothing); an explicit `null` clears a field (see `TaskUpdateSchema`). A task in the
    Trash is a 400: restore it first. Changing `rrule` or `recurrence_mode` edits the repeat rule
    (an empty `rrule` stops repeating), and a new `due_date` moves the task's reminders.

    `state` sets an open task's state (`todo`, `in_progress` or `on_hold`) and `blocked_by`
    replaces the set of tasks that block it (see `TaskUpdateSchema`); the blockers decide the
    `blocked` state, so a task with an open blocker is `blocked` after the call and one left with
    none goes back to `todo`.

    The fields are checked before `TODO_EDIT_USER` is spent, so a bad value costs nothing. What
    only the locked write can decide (the task was trashed or closed meanwhile, a task with
    subtasks moved under a parent, a subtask given a repeat rule, a link another request made first
    that now closes a cycle) is refused after it was spent."""
    task = owned_task(request, number)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return task_response(request, task)
    for key in ('title', 'state'):
        if key in changes and changes[key] is None:
            raise HttpError(400, f'{key}: Cannot be null.')
    if changes.get('priority', 1) is None:
        changes['priority'] = 0
    if 'description' in changes and changes['description'] is None:
        changes['description'] = ''
    if 'labels' in changes and changes['labels'] is None:
        changes['labels'] = []
    if 'rrule' in changes and changes['rrule'] is None:
        changes['rrule'] = ''
    new_state = changes.pop('state', None)
    set_blockers = 'blocked_by' in changes
    wanted = changes.pop('blocked_by', None)
    with mapped_errors():
        if 'parent' in changes:
            changes['parent'] = task_from_body(request.auth, changes['parent'])
        if changes:
            services.check_update_task(task, changes=changes)  # a refusal here spends no budget
        if new_state is not None:
            new_state = services.tasks.clean_state(new_state)
            if task.is_trashed:
                raise ValidationError('Restore the task before changing it.')
            if task.is_completed:
                raise ValidationError(NOT_OPEN_MESSAGE)
        to_add, to_remove = _plan_blockers(task, request.auth, wanted) if set_blockers else ([], [])
        charge(request, 'TODO_EDIT_USER')
        with transaction.atomic():  # the fields, the state and the links stand or fall together
            if changes:
                services.update_task(task, changes=changes)
            if new_state is not None:
                services.set_state(task, new_state)
            for other in to_remove:
                blockers.remove_blocker(task, other)
            for other in to_add:
                blockers.add_blocker(task, other)
    return task_response(request, task)


@router.delete('/tasks/{number}', response={204: None})
def trash_task(request, number: int):
    """Move a task, and its subtasks, to the Trash (restorable until `trash_days` have passed).
    Idempotent: a task already in the Trash stays there. Nothing is refunded."""
    task = owned_task(request, number)
    charge(request, 'TODO_EDIT_USER')
    services.trash_task(task)
    return 204, None


def _act(request, number: int, action, *, trashed: bool | None = None) -> dict[str, Any]:
    """Look the task up (a 404), spend the edit budget, run `action(task)` and answer with the task
    as it is now."""
    task = owned_task(request, number, trashed=trashed)
    charge(request, 'TODO_EDIT_USER')
    with mapped_errors():
        action(task)
    return task_response(request, task)


def _refuse_trashed(task: Task) -> None:
    if task.is_trashed:
        raise ValidationError('Restore the task before changing it.')


@router.post('/tasks/{number}/close', response=TaskSchema)
def close_task(request, number: int, payload: CloseSchema):
    """Close a task as Done (the default) or as Won't do (`resolution`), and deal with its open
    subtasks (`subtasks`: closed as Done by default, closed as Won't do, or left open). Every field is
    optional, so `{}` closes the task and its open subtasks as Done. Closing a
    repeating task creates its next occurrence **whichever the resolution** (Won't do means "skip
    this one"); one the balance can't cover is created unpaid, see `POST /tasks`. A closed task
    has no state: it goes back to `todo` and is `blocked` again only if it is reopened while a
    blocker is open. What the task blocks is freed when this was its last open blocker.

    `note` (only with `resolution=wont_do`, else a 400) is added as a comment: it spends
    `TODO_COMMENT_USER` as well as `TODO_EDIT_USER`.

    The API closes without asking about open blockers or subtasks that have some: the links are
    kept. Closing a task that is already closed changes nothing (`resolution` is **not** applied:
    use `POST /tasks/{number}/resolution`) except that a `note` is still added as a comment; either
    way the answer is the task as it is. A task in the Trash is a 400.

    Everything is checked before the budgets are spent, so a refusal costs nothing."""
    task = owned_task(request, number)
    with mapped_errors():
        resolution = services.tasks.clean_resolution(payload.resolution)
        subtasks = services.tasks.clean_subtask_action(payload.subtasks)
        note = services.tasks.clean_note(payload.note)
        if note and resolution != Resolution.WONT_DO:
            raise ValidationError({'note': "A note can only be added when closing as Won't do."})
        _refuse_trashed(task)
    rules = ['TODO_EDIT_USER'] + (['TODO_COMMENT_USER'] if note else [])
    for rule in rules:  # both are looked at first, so a refusal spends neither
        ratelimit.enforce(ratelimit.peek_value(str(request.auth.pk), rule))
    for rule in rules:
        charge(request, rule)
    with mapped_errors():
        services.close_task(task, resolution, subtasks=subtasks, note=note)
    return task_response(request, task)


@router.post('/tasks/{number}/reopen', response=TaskSchema)
def reopen_task(request, number: int):
    """Make a closed task open again, whichever way it was closed: `blocked` when one of its
    blockers is still open, else `todo`; its resolution is cleared. The next occurrence of a
    repeating task, if one was created, stays; the open tasks it blocks are told it is back and
    keep their state. Reopening an open task changes nothing. A task in the Trash is a 400."""
    task = owned_task(request, number)
    with mapped_errors():
        _refuse_trashed(task)
    return _act(request, number, services.uncomplete_task)


@router.post('/tasks/{number}/resolution', response=TaskSchema)
def set_resolution(request, number: int, payload: ResolutionSchema):
    """Change how a **closed** task was closed (`done` or `wont_do`). It creates no occurrence,
    touches no subtasks and keeps the time the task was closed at. A 400 for an open task (it has
    no resolution; close it instead) and for a task in the Trash."""
    task = owned_task(request, number)
    with mapped_errors():  # a refusal here spends no budget
        resolution = services.tasks.clean_resolution(payload.resolution)
        _refuse_trashed(task)
        if task.completed_at is None:
            raise ValidationError(services.tasks.NOT_CLOSED_MESSAGE)
    return _act(request, number, lambda task: services.set_resolution(task, resolution))


@router.post('/tasks/{number}/state', response=TaskSchema)
def set_state(request, number: int, payload: StateSchema):
    """Set an open task's state: `todo`, `in_progress` or `on_hold`. Each task has its own state:
    nothing propagates between a parent and its subtasks. A 400 for `blocked` (only blockers set
    it; a Blocked task can still be given any of the three, which keeps its links), for a closed
    task (reopen it first) and for a task in the Trash. Leaving On hold or Blocked delivers one
    reminder for what was missed while it was muted."""
    task = owned_task(request, number)
    with mapped_errors():  # a refusal here spends no budget
        state = services.tasks.clean_state(payload.state)
        _refuse_trashed(task)
        if task.completed_at is not None:
            raise ValidationError(NOT_OPEN_MESSAGE)
    return _act(request, number, lambda task: services.set_state(task, state))


@router.post('/tasks/{number}/restore', response=TaskSchema)
def restore_task(request, number: int):
    """Take a task out of the Trash, with the subtasks trashed along with it. A 400 while its
    parent is still in the Trash. Idempotent."""
    return _act(request, number, services.restore_task)


@router.post('/tasks/{number}/delete-forever', response={204: None})
def delete_forever(request, number: int):
    """Delete a task from the Trash for good, with its subtasks, comments and files. A 404 for a
    task that isn't in the Trash; a 400 for one whose price is still owed (a top-up settles it
    first)."""
    task = owned_task(request, number, trashed=True)
    charge(request, 'TODO_EDIT_USER')
    with mapped_errors():
        services.delete_forever(task)
    return 204, None


@router.post('/tasks/{number}/postpone', response=TaskSchema)
def postpone_task(request, number: int, payload: PostponeSchema):
    """Move the due date: `tomorrow`, `next_week` (the next Monday), `next_weekend` (the next
    Saturday) or `date` with `date` (today or later), keeping the time. Postponing one occurrence
    never moves the schedule of a repeating task."""
    task = owned_task(request, number)
    with mapped_errors():  # a refusal here spends no budget
        if not task.is_open:
            raise ValidationError('Only open tasks can be postponed.')
        services.tasks.postpone_target(user_today(request.auth), payload.preset, payload.date)
    return _act(
        request,
        number,
        lambda task: services.postpone_task(task, preset=payload.preset, date=payload.date),
    )


@router.post('/tasks/{number}/checklist', response=TaskSchema)
def toggle_checklist(request, number: int, payload: ChecklistSchema):
    """Tick or untick a checklist item of the description (`- [ ] item`), counted from 0; the box
    is written back to the text."""
    return _act(
        request,
        number,
        lambda task: services.toggle_checklist(task, payload.index, payload.checked),
    )


@router.post('/tasks/{number}/recurrence/stop', response=TaskSchema)
def stop_recurrence(request, number: int):
    """Stop repeating: the rule ends and the task stays. Idempotent."""
    return _act(request, number, services.recurrence.stop)
