"""Creating, changing, closing, postponing, trashing and ordering tasks (issue #178).

Every rule lives here, so the web views (and later the API) enforce the same ones; a refusal is a
`django.core.exceptions.ValidationError` keyed by field (`'__all__'` for one that belongs to no
field), and `InsufficientCreditsError` (`apps.billing.services`) for a task the balance can't
cover. Trashing, closing and the like never refund what a task cost.

**States and resolutions** (issue #272). An open task has a `state` (To do, In progress, On hold:
`set_state`); a closed one a `resolution` (Done, Won't do: `close_task`, `set_resolution`), with
`completed_at` meaning "closed at" for both. Closing resets the state to To do (a closed task has no
state to keep) and reopening (`uncomplete_task`) clears the resolution. `complete_task` is "close as
Done", which is what the circle, the Complete button, the push notification and the API all do.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Any
from uuid import UUID

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today
from apps.billing.services import InsufficientCreditsError
from apps.core.markdown import toggle_checklist_item

from ..models import (
    SELECTABLE_STATES,
    Label,
    Priority,
    Recurrence,
    RecurrenceMode,
    Resolution,
    Task,
    TaskSource,
    TaskState,
    TodoSettings,
)
from . import attachments, blockers, comments, dates, reminders
from . import recurrence as recurrence_service
from .accounts import allocate_number, get_account, lock_account
from .billing import cannot_afford, charge_task_creation
from .dates import next_position, next_today_position
from .limits import DESCRIPTION_MAX, TITLE_MAX
from .references import sync_references
from .task_state import lock_tasks, write_state

logger = logging.getLogger(__name__)

#: The fields `update_task` accepts.
UPDATABLE = frozenset(
    {
        'title',
        'description',
        'priority',
        'due_date',
        'due_time',
        'labels',
        'parent',
        'rrule',
        'recurrence_mode',
    }
)

#: What to do with a parent's open subtasks when it is closed: close them as Done or as Won't do,
#: or leave them open. Where nobody can be asked (the push notification, the API) it is Done.
SUBTASKS_DONE = 'done'
SUBTASKS_WONT_DO = 'wont_do'
SUBTASKS_LEAVE = 'leave'
SUBTASK_ACTIONS = (SUBTASKS_DONE, SUBTASKS_WONT_DO, SUBTASKS_LEAVE)

POSTPONE_PRESETS = ('tomorrow', 'next_week', 'next_weekend', 'date')
REORDER_SCOPES = ('today', 'subtasks')

UNPAID_MESSAGE = (
    'This task has not been paid for yet. Top up your credits first: it can be deleted forever '
    'once it is settled.'
)
FILES_NOT_DELETED_MESSAGE = "The task's files could not be deleted right now. Try again later."
BLOCKED_MESSAGE = 'Top up your credits to add tasks: a repeating task is waiting to be paid.'
TOO_MANY_MESSAGE = 'Too many open tasks. Complete or delete some to add more.'
STATE_MESSAGE = 'Choose To do, In progress or On hold.'
RESOLUTION_MESSAGE = "Choose Done or Won't do."
SUBTASKS_MESSAGE = "Choose whether to close the subtasks as Done, as Won't do, or leave them open."
NOT_CLOSED_MESSAGE = 'Only a closed task has a resolution to change.'
NOT_OPEN_MESSAGE = 'Reopen the task before changing its state.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


class TaskCreationBlockedError(ValidationError):
    """New tasks are refused until a top-up settles the repeating-task occurrences that could not
    be paid for (`TodoAccount.creation_blocked_at`). A `ValidationError`, so a caller that does not
    care why handles it as any other refusal; the API answers it with a 409 and the email intake
    with `rejected_credits`."""

    def __init__(self) -> None:
        super().__init__({NON_FIELD_ERRORS: BLOCKED_MESSAGE})


class TooManyTasksError(ValidationError):
    """The user already has the most open tasks allowed (`TodoSettings.max_open_tasks_per_user`)."""

    def __init__(self) -> None:
        super().__init__({NON_FIELD_ERRORS: TOO_MANY_MESSAGE})


# -- Validation ----------------------------------------------------------------------------------


def clean_title(title: object) -> str:
    cleaned = str(title or '').strip()
    if not cleaned:
        raise _error('title', 'Title is required.')
    if len(cleaned) > TITLE_MAX:
        raise _error('title', f'Title is too long (at most {TITLE_MAX:,} characters).')
    return cleaned


def clean_description(description: object) -> str:
    # A browser submits `\r\n`, which would count twice against the cap.
    cleaned = str(description or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if len(cleaned) > DESCRIPTION_MAX:
        raise _error(
            'description', f'Description is too long (at most {DESCRIPTION_MAX:,} characters).'
        )
    return cleaned


def clean_priority(priority: object) -> int:
    try:
        value = int(priority)  # ty: ignore[invalid-argument-type]
    except TypeError, ValueError:
        value = -1
    if value not in Priority.values:
        raise _error('priority', 'Choose priority 1, 2 or 3, or none.')
    return value


def clean_due(due_date: object, due_time: object) -> tuple[date | None, time | None]:
    if due_date is not None and not isinstance(due_date, date):
        raise _error('due_date', 'Enter a valid date.')
    if due_time is not None and not isinstance(due_time, time):
        raise _error('due_time', 'Enter a valid time.')
    if due_time is not None and due_date is None:
        raise _error('due_time', 'A time needs a date.')
    return due_date, due_time


def clean_labels(user: User, labels: Iterable[Label | str | UUID]) -> list[Label]:
    """The user's labels named by `labels` (labels or their ids); any other is refused."""
    wanted = {label.pk if isinstance(label, Label) else label for label in labels}
    if not wanted:
        return []
    try:
        found = list(Label.objects.filter(user=user, pk__in=wanted))
    except ValueError, ValidationError:  # a malformed id
        raise _error('labels', 'Unknown label.') from None
    if len(found) != len(wanted):
        raise _error('labels', 'Unknown label.')
    return found


def clean_state(state: object) -> str:
    """`state` as one of the states a person can choose, else a refusal. Blocked is not one: only a
    blocker sets it (`services.blockers`)."""
    value = str(state or TaskState.TODO)
    if value not in SELECTABLE_STATES:
        raise _error('state', STATE_MESSAGE)
    return value


def clean_resolution(resolution: object) -> str:
    value = str(resolution or '')
    if value not in Resolution.values:
        raise _error('resolution', RESOLUTION_MESSAGE)
    return value


def clean_subtask_action(action: object) -> str:
    """What to do with the open subtasks of a parent being closed; nothing said means Done (as the
    circle, the push notification and the API have always done)."""
    value = str(action or SUBTASKS_DONE)
    if value not in SUBTASK_ACTIONS:
        raise _error('subtasks', SUBTASKS_MESSAGE)
    return value


def clean_note(note: object) -> str:
    """The optional note of "Close as won't do": stripped, `\\r\\n` normalised, at most a comment's
    length (it becomes a comment); empty means none."""
    cleaned = str(note or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if len(cleaned) > comments.COMMENT_MAX:
        raise _error('note', f'Note is too long (at most {comments.COMMENT_MAX:,} characters).')
    return cleaned


def _locked_parent(parent: Task) -> Task:
    """`parent` re-read under its row lock (inside a transaction), or a refusal if it is gone."""
    locked = lock_tasks(parent.pk).get(parent.pk)
    if locked is None:
        raise _error('parent', 'Unknown parent task.')
    return locked


def clean_parent(user: User, parent: Task | None) -> Task | None:
    """`parent` if a subtask may go under it: the user's own open, non-trashed top-level task."""
    if parent is None:
        return None
    if parent.user_id != user.pk:
        raise _error('parent', 'Unknown parent task.')
    if parent.parent_id is not None:
        raise _error('parent', 'A subtask cannot have subtasks.')
    if not parent.is_open:
        raise _error('parent', 'Choose an open task as the parent.')
    return parent


# -- Create and update ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CleanedTask:
    """What `create_task` writes, after every field has been validated."""

    title: str
    description: str
    priority: int
    due_date: date | None
    due_time: time | None
    labels: list[Label]
    parent: Task | None
    rule: str
    mode: str
    state: str = TaskState.TODO
    blockers: list[Task] = field(default_factory=list)


def _clean_new_task(
    user: User,
    *,
    title: str,
    description: str,
    priority: int,
    due_date: date | None,
    due_time: time | None,
    parent: Task | None,
    labels: Iterable[Label | str | UUID],
    rrule: str,
    recurrence_mode: str,
    recurrence: Recurrence | None,
    state: str = TaskState.TODO,
    blocked_by: Iterable[int] = (),
) -> _CleanedTask:
    """Validate the fields of a new task (no writes, no locks): what `create_task` and
    `check_new_task` share."""
    cleaned_title = clean_title(title)
    cleaned_description = clean_description(description)
    cleaned_priority = clean_priority(priority)
    cleaned_date, cleaned_time = clean_due(due_date, due_time)
    cleaned_labels = clean_labels(user, labels)
    cleaned_parent = clean_parent(user, parent)
    cleaned_rule = recurrence_service.clean_rule(rrule, user_today(user)) if rrule else ''
    cleaned_mode = recurrence_service.clean_mode(recurrence_mode)
    cleaned_state = clean_state(state)
    cleaned_blockers = blockers.clean_new(user, blocked_by)
    if (cleaned_rule or recurrence is not None) and cleaned_parent is not None:
        raise _error('rrule', 'A subtask cannot repeat.')
    if recurrence is not None and (cleaned_rule or recurrence.user_id != user.pk):
        raise _error('rrule', 'Unknown repeat rule.')
    if cleaned_rule and cleaned_date is None:
        cleaned_date = recurrence_service.first_date(cleaned_rule, user_today(user))
    return _CleanedTask(
        title=cleaned_title,
        description=cleaned_description,
        priority=cleaned_priority,
        due_date=cleaned_date,
        due_time=cleaned_time,
        labels=cleaned_labels,
        parent=cleaned_parent,
        rule=cleaned_rule,
        mode=cleaned_mode,
        state=cleaned_state,
        blockers=cleaned_blockers,
    )


def check_new_task(
    user: User,
    *,
    title: str,
    description: str = '',
    priority: int = 0,
    due_date: date | None = None,
    due_time: time | None = None,
    parent: Task | None = None,
    labels: Iterable[Label | str | UUID] = (),
    rrule: str = '',
    recurrence_mode: str = RecurrenceMode.FIXED,
    state: str = TaskState.TODO,
    blocked_by: Iterable[int] = (),
) -> date | None:
    """Everything `create_task` would refuse that can be told without writing anything, in the same
    order: the fields (`ValidationError`), a creation blocked by an unpaid repeating task
    (`TaskCreationBlockedError`), the cap on open tasks (`TooManyTasksError`) and a balance that
    can't pay for the task (`InsufficientCreditsError`). Returns the task's due date as it would be
    created (the rule's first date for a repeating task without one).

    For a caller that wants to refuse before spending something (a rate-limit budget, the AI call
    that reads the title): it takes no lock, so `create_task` still decides for good under the
    account lock, and a race (the cap or the balance changing in between) can still refuse there.
    """
    cleaned = _clean_new_task(
        user,
        title=title,
        description=description,
        priority=priority,
        due_date=due_date,
        due_time=due_time,
        parent=parent,
        labels=labels,
        rrule=rrule,
        recurrence_mode=recurrence_mode,
        recurrence=None,
        state=state,
        blocked_by=blocked_by,
    )
    settings = TodoSettings.load()
    account = get_account(user)
    if account.creation_blocked_at is not None:
        raise TaskCreationBlockedError
    if dates.open_tasks(user).count() >= settings.max_open_tasks_per_user:
        raise TooManyTasksError
    if cannot_afford(account, settings):
        raise InsufficientCreditsError('Insufficient credits')
    return cleaned.due_date


def create_task(
    user: User,
    *,
    title: str,
    description: str = '',
    priority: int = 0,
    due_date: date | None = None,
    due_time: time | None = None,
    parent: Task | None = None,
    labels: Iterable[Label | str | UUID] = (),
    rrule: str = '',
    recurrence_mode: str = RecurrenceMode.FIXED,
    recurrence: Recurrence | None = None,
    state: str = TaskState.TODO,
    blocked_by: Iterable[int] = (),
    source: str = TaskSource.WEB,
    email_from: str = '',
    email_from_authenticated: bool = False,
    allow_unpaid: bool = False,
) -> Task:
    """Create a task (or a subtask under `parent`), numbered and charged.

    Validation first (nothing is written for a refusal), then one transaction that locks the
    user's account row, takes the next number, counts and charges the task (the free allowance,
    then the price) and writes the row. A balance that can't cover it raises
    `InsufficientCreditsError` and rolls all of that back. `allow_unpaid` (for a task the user did
    not ask for) creates it anyway, owed, and blocks new tasks until it is settled
    (`services.billing`).

    A repeating task is created with `rrule` (and `recurrence_mode`): a new `Recurrence` whose
    anchor is the task's date, which is the rule's first date on or after today when none is given.
    `recurrence` joins the task to an existing one (the next occurrence, `services.recurrence`).
    Only a top-level task can repeat.

    Every new task is To do unless the caller says otherwise (`state`: the quick-add box offers it);
    the next occurrence of a repeating task is always To do, with no blockers. `blocked_by` (task
    numbers, the quick-add box's "Blocked by") links the new task to those of the user's tasks and
    makes it Blocked when one of them is open (`services.blockers`).

    `email_from` and `email_from_authenticated` (a task made from an email, issue #186) are stored
    on the task so its page needs no lookup of the email: the From address and whether SES
    authenticated the sender.
    """
    cleaned = _clean_new_task(
        user,
        title=title,
        description=description,
        priority=priority,
        due_date=due_date,
        due_time=due_time,
        parent=parent,
        labels=labels,
        rrule=rrule,
        recurrence_mode=recurrence_mode,
        recurrence=recurrence,
        state=state,
        blocked_by=blocked_by,
    )
    cleaned_title, cleaned_description = cleaned.title, cleaned.description
    cleaned_priority, cleaned_time = cleaned.priority, cleaned.due_time
    cleaned_date, cleaned_labels = cleaned.due_date, cleaned.labels
    cleaned_parent, cleaned_rule, cleaned_mode = cleaned.parent, cleaned.rule, cleaned.mode
    settings = TodoSettings.load()
    account = get_account(user)
    with transaction.atomic():
        number = allocate_number(account)  # locks the account row; refreshes `account`
        if account.creation_blocked_at is not None and not allow_unpaid:
            raise TaskCreationBlockedError
        if cleaned_parent is not None:
            # Locked and re-read: a concurrent move of the parent under another task (`update_task`)
            # would otherwise leave this a subtask of a subtask.
            cleaned_parent = clean_parent(user, _locked_parent(cleaned_parent))
        if (
            dates.open_tasks(user).count() >= settings.max_open_tasks_per_user
        ):  # under the lock, so racing creations can't pass the cap together
            raise TooManyTasksError
        charged, unpaid = charge_task_creation(
            account, settings, number=number, allow_unpaid=allow_unpaid
        )
        if cleaned_rule:
            assert cleaned_date is not None  # filled above
            recurrence = recurrence_service.start(user, cleaned_rule, cleaned_mode, cleaned_date)
        task = Task(
            user=user,
            number=number,
            parent=cleaned_parent,
            title=cleaned_title,
            description=cleaned_description,
            priority=cleaned_priority,
            due_date=cleaned_date,
            due_time=cleaned_time,
            recurrence=recurrence,
            recurrence_anchor=cleaned_date if recurrence is not None else None,
            state=cleaned.state,
            source=source,
            email_from=email_from[:320],
            email_from_authenticated=email_from_authenticated,
            charged_credits=charged,
            unpaid_at=timezone.now() if unpaid else None,
            position=next_position(cleaned_parent),
            today_position=next_today_position(user, cleaned_date),
        )
        task.save()
        if cleaned_labels:
            task.labels.set(cleaned_labels)
        if cleaned_description:
            sync_references(task)
        blockers.link_new_task(task, cleaned.blockers)
        reminders.apply_defaults(task, account)
    return task


def _clean_changes(
    task: Task, changes: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Label] | None, Task | None, bool]:
    """Validate a partial update against the task as read (no writes, no locks): `(values, labels,
    new_parent, moves)`. What `update_task` and `check_update_task` share."""
    unknown = set(changes) - UPDATABLE
    if unknown:
        raise ValueError(f'Cannot update {sorted(unknown)}')
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')

    values: dict[str, Any] = {}
    if 'title' in changes:
        values['title'] = clean_title(changes['title'])
    if 'description' in changes:
        values['description'] = clean_description(changes['description'])
    if 'priority' in changes:
        values['priority'] = clean_priority(changes['priority'])
    if 'due_date' in changes or 'due_time' in changes:
        new_date = changes['due_date'] if 'due_date' in changes else task.due_date
        new_time = changes['due_time'] if 'due_time' in changes else task.due_time
        if 'due_date' in changes and new_date is None:
            new_time = None
        values['due_date'], values['due_time'] = clean_due(new_date, new_time)
        if values['due_date'] != task.due_date:
            values['today_position'] = 0  # a place in another day's Today order means nothing
    labels = clean_labels(task.user, changes['labels']) if 'labels' in changes else None
    new_parent: Task | None = None
    moves = False
    if 'parent' in changes:
        new_parent = clean_parent(task.user, changes['parent'])
        if new_parent is not None and new_parent.pk == task.pk:
            raise _error('parent', 'A task cannot be its own parent.')
        moves = new_parent != task.parent
        if moves:
            values['parent'] = new_parent
    return values, labels, new_parent, moves


def check_update_task(task: Task, *, changes: Mapping[str, Any]) -> None:
    """What `update_task` would refuse that can be told without writing anything: an unknown key
    (`ValueError`), a trashed task, an invalid field, a bad repeat rule, a task moved under itself.
    For a caller that wants to refuse before spending a rate-limit budget.

    It reads the task as the caller loaded it and takes no lock, so what `update_task` only decides
    under the row lock (the task has been trashed, completed or moved since; a task with subtasks
    moved under a parent; a subtask given a repeat rule) can still be refused there.
    """
    _clean_changes(task, changes)
    _rule_action(task, changes)  # the repeat rule's syntax


def update_task(task: Task, *, changes: Mapping[str, Any]) -> Task:
    """Apply a partial update (`UPDATABLE` keys only) with the same validation as `create_task`.

    Clearing `due_date` clears `due_time` too. A task moves under a `parent` only when it has no
    subtasks of its own; `parent=None` makes a subtask a top-level task. A trashed task is
    read-only.

    `rrule` / `recurrence_mode` edit the repeat rule (`services.recurrence`): a rule that differs
    from the running one replaces it (the anchor becomes the task's date, which is filled with the
    rule's first date when the task has none); the same rule and mode change nothing, the anchor
    included; an empty `rrule` stops repeating. A change also makes this task the occurrence whose
    completion creates the next one (`Task.recurrence_anchor`) and starts the `COUNT` again.
    """
    values, labels, new_parent, moves = _clean_changes(task, changes)

    with transaction.atomic():
        lock_account(task.user_id)  # first: see `services.blockers`' note on locking
        # The task's row (and the new parent's, in pk order) is locked and re-read first: the
        # checks below read rows another request may be changing (a subtask being added to this
        # task, the new parent moving under something), and the description is compared with the
        # text stored *now*, so the references written always match the text that was saved.
        new_parent_pk = new_parent.pk if moves and new_parent is not None else None
        locked = lock_tasks(*(pk for pk in (task.pk, new_parent_pk) if pk is not None))
        current = locked.get(task.pk)
        if current is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        if current.is_trashed:
            raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
        # The repeat rule is judged against the locked row too: whether the task is completed, a
        # subtask or already repeating is what is stored *now*.
        rule_action = _rule_action(current, changes)
        if rule_action is not None and rule_action[0] == 'set':
            if current.is_completed:
                raise _error('rrule', 'Only an open task can repeat.')
            if (current.parent_id is not None and not (moves and new_parent is None)) or (
                moves and new_parent is not None
            ):
                raise _error('rrule', 'A subtask cannot repeat.')
            anchor = values['due_date'] if 'due_date' in values else current.due_date
            if anchor is None:
                anchor = values['due_date'] = recurrence_service.first_date(
                    rule_action[1], user_today(task.user)
                )
                values['today_position'] = 0
            values['recurrence'] = recurrence_service.change(
                current, rule_action[1], rule_action[2], anchor
            )
            values['recurrence_anchor'] = anchor
        elif moves and new_parent is not None and current.active_recurrence is not None:
            raise _error('parent', 'A repeating task cannot become a subtask.')
        if moves:
            if new_parent is not None:
                if new_parent.pk not in locked:
                    raise _error('parent', 'Unknown parent task.')
                clean_parent(task.user, locked[new_parent.pk])
                if Task.objects.filter(parent=task).exists():
                    raise _error('parent', 'A task with subtasks cannot become a subtask.')
            values['position'] = next_position(new_parent)
        description_changed = (
            'description' in values and values['description'] != current.description
        )
        due_changed = ('due_date' in values and values['due_date'] != current.due_date) or (
            'due_time' in values and values['due_time'] != current.due_time
        )
        had_date = current.due_date is not None
        for name, value in values.items():
            setattr(task, name, value)
        task.save(update_fields=[*values, 'updated_at'])
        if labels is not None:
            task.labels.set(labels)
        if description_changed:
            sync_references(task)
        if due_changed:
            reminders.on_due_changed(task, had_date=had_date)
        if rule_action is not None and rule_action[0] == 'stop':
            recurrence_service.stop(current)
            task.recurrence = current.recurrence
    return task


def _rule_action(task: Task, changes: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """What `changes` do to the task's repeat rule: `('set', rule, mode)`, `('stop', '', '')`, or
    `None` for nothing (no repeat keys, or the very rule and mode the task already has)."""
    if 'rrule' not in changes and 'recurrence_mode' not in changes:
        return None
    active = task.active_recurrence
    if 'rrule' in changes:
        if not changes['rrule']:
            return ('stop', '', '') if active is not None else None
        rule = recurrence_service.clean_rule(changes['rrule'], user_today(task.user))
    elif active is not None:
        rule = active.rrule
    else:
        return None  # a mode with no rule to apply it to
    if changes.get('recurrence_mode'):
        mode = recurrence_service.clean_mode(changes['recurrence_mode'])
    else:
        mode = active.mode if active is not None else RecurrenceMode.FIXED
    if active is not None and active.rrule == rule and active.mode == mode:
        return None
    return ('set', rule, mode)


def toggle_checklist(task: Task, index: int, checked: bool) -> Task:
    """Tick or untick the `index`th checklist item of the description (written back to the text).

    The toggle is applied to the description as it is *now*: the row is re-read under its lock, so
    two boxes ticked at once (two requests) both survive instead of the second overwriting the
    first. Returns the fresh task."""
    with transaction.atomic():
        lock_account(task.user_id)  # before the task's row, as `update_task` (called below) does
        fresh = Task.objects.select_for_update().filter(pk=task.pk).first()
        if fresh is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        try:
            description = toggle_checklist_item(fresh.description, index, checked)
        except IndexError, ValueError:
            raise _error('index', 'No such checklist item.') from None
        return update_task(fresh, changes={'description': description})


# -- Closing and states --------------------------------------------------------------------------


def close_task(
    task: Task,
    resolution: str = Resolution.DONE,
    *,
    subtasks: str = SUBTASKS_DONE,
    note: str = '',
) -> Task:
    """Close the task as Done or Won't do, in one transaction. A task that is already closed is left
    as it is (`set_resolution` changes how it was closed).

    The state goes back to To do (a closed task has no state), `completed_at` is "closed at", and a
    repeating task creates its next occurrence **whatever the resolution** -- Won't do means "skip
    this one" -- before the open subtasks are dealt with, so they are copied to it
    (`services.recurrence.on_completed`). `subtasks` says what happens to the open, non-trashed
    subtasks: closed as Done (the default: what the circle, the push notification and the API do),
    closed as Won't do, or left open. A non-empty `note` becomes a comment on the task (its length
    rules; refused up front, before anything is written).
    """
    resolution = clean_resolution(resolution)
    subtasks = clean_subtask_action(subtasks)
    note = clean_note(note)
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before completing it.')
    now = timezone.now()
    with transaction.atomic():
        # Account, then the closing task, then its subtasks, then (in `blockers.ended`) what it
        # blocks: `services.blockers`' note on locking. The account first also because the next
        # occurrence (`recurrence.on_completed` -> `create_task`) takes it anyway.
        lock_account(task.user_id)
        lock_tasks(task.pk)
        won = Task.objects.filter(
            pk=task.pk, completed_at__isnull=True, deleted_at__isnull=True
        ).update(completed_at=now, resolution=resolution, state=TaskState.TODO, updated_at=now)
        if not won:
            task.refresh_from_db()
            if task.is_trashed:
                raise _error(NON_FIELD_ERRORS, 'Restore the task before completing it.')
            if note and task.is_completed:
                # Lost a race with another close: the task is closed as someone else chose, but
                # what the user typed is not thrown away.
                comments.add_comment(task, task.user, note)
            # A transient flag for the caller (the view tells the user); never stored.
            task.already_closed = True
            return task
        recurrence_service.on_completed(task)
        ended = [task.pk]
        if subtasks != SUBTASKS_LEAVE:
            open_subtasks = Task.objects.filter(
                parent=task, completed_at__isnull=True, deleted_at__isnull=True
            )
            subtask_ids = list(open_subtasks.values_list('pk', flat=True))
            if subtask_ids:
                lock_tasks(*subtask_ids)
            ended += subtask_ids
            open_subtasks.update(
                completed_at=now, resolution=subtasks, state=TaskState.TODO, updated_at=now
            )
        if note:
            comments.add_comment(task, task.user, note)
        # What they blocked is free of them now (an open blocker that closes with its parent too).
        blockers.ended(ended, now)
    task.completed_at = now
    task.resolution = resolution
    task.state = TaskState.TODO
    task.updated_at = now
    return task


def complete_task(task: Task, *, subtasks: str = SUBTASKS_DONE) -> Task:
    """Close the task as Done (see `close_task`): what Complete does everywhere."""
    return close_task(task, Resolution.DONE, subtasks=subtasks)


def set_resolution(task: Task, resolution: str) -> Task:
    """Change how a closed task was closed ("Mark as done" / "Mark as won't do"). It creates no
    occurrence, touches no subtasks and keeps the time it was closed at."""
    resolution = clean_resolution(resolution)
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
    now = timezone.now()
    with transaction.atomic():
        lock_account(task.user_id)
        changed = Task.objects.filter(
            pk=task.pk, completed_at__isnull=False, deleted_at__isnull=True
        ).update(resolution=resolution, updated_at=now)
    if not changed:
        if Task.objects.filter(pk=task.pk, deleted_at__isnull=False).exists():
            raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
        raise _error(NON_FIELD_ERRORS, NOT_CLOSED_MESSAGE)
    task.resolution = resolution
    task.updated_at = now
    return task


def uncomplete_task(task: Task) -> Task:
    """Reopen a closed task, whatever its resolution was: Blocked when one of its blockers is still
    open, else To do (`services.blockers`). Its parent, if it has one, stays as it is. The open tasks
    it blocks get a notice that it is back (they are not blocked again)."""
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
    now = timezone.now()
    with transaction.atomic():
        lock_account(task.user_id)
        # The row is locked before the blockers are read: a blocker closing at the same moment has
        # committed (and is read closed) or comes after this commit and finds the task open.
        current = lock_tasks(task.pk).get(task.pk)
        if current is not None and current.completed_at is not None and not current.is_trashed:
            state = TaskState.BLOCKED if blockers.has_open_blocker(task.pk) else TaskState.TODO
            Task.objects.filter(pk=task.pk).update(
                completed_at=None, resolution='', state=state, updated_at=now
            )
            blockers.reopened([task.pk])
    task.refresh_from_db()
    return task


def set_state(task: Task, state: str) -> Task:
    """Set an open task's state (To do, In progress, On hold; Blocked is not chosen, only blockers
    set it). Each task has its own: nothing propagates between a parent and its subtasks. A closed or
    trashed task has none to change. A Blocked task can be given any of the three: its links stay.

    Leaving a muted state releases the reminders held meanwhile
    (`services.reminders.release_held`: one reminder, on the channels they used). A task due today
    that moves to another group goes to the end of it when Today has a manual order."""
    state = clean_state(state)
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
    now = timezone.now()
    with transaction.atomic():
        lock_account(task.user_id)
        current = lock_tasks(task.pk).get(task.pk)
        if current is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        if current.is_trashed:
            raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
        if current.completed_at is not None:
            raise _error(NON_FIELD_ERRORS, NOT_OPEN_MESSAGE)
        changed = write_state(current, state, now)
        if changed:
            task.today_position = current.today_position
    task.state = state
    if changed:
        task.updated_at = now
    return task


# -- Postponing ----------------------------------------------------------------------------------


def postpone_target(today: date, preset: str, chosen: date | None = None) -> date:
    """The date a postpone preset means on `today`: `tomorrow`, `next_week` (the next Monday after
    today) or `next_weekend` (the next Saturday after today); `date` is `chosen` (today or later)."""
    if preset == 'tomorrow':
        return today + timedelta(days=1)
    if preset == 'next_week':
        return today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    if preset == 'next_weekend':
        return today + timedelta(days=(5 - today.weekday()) % 7 or 7)
    if preset == 'date':
        if chosen is None:
            raise _error('date', 'Choose a date.')
        if chosen < today:
            raise _error('date', 'Choose today or a later date.')
        return chosen
    raise _error('preset', 'Choose when to postpone the task to.')


def postpone_task(task: Task, *, preset: str, date: date | None = None) -> Task:
    """Move the due date to a preset or a chosen date, keeping the time. "Today" is the owner's.
    A task with no due date gets one. Postponing one occurrence never moves a repeating
    schedule (that is the recurrence sub-issue's rule; nothing here touches it). Its reminders
    follow the new date."""
    if not task.is_open:
        raise _error(NON_FIELD_ERRORS, 'Only open tasks can be postponed.')
    target = postpone_target(user_today(task.user), preset, date)
    had_date = task.due_date is not None
    moved = target != task.due_date
    with transaction.atomic():
        if moved:
            task.due_date = target
            task.today_position = 0  # a place in another day's Today order means nothing
        task.save(update_fields=['due_date', 'today_position', 'updated_at'])
        if moved:  # its reminders follow the new date (a task with no date gets the default one)
            reminders.on_due_changed(task, had_date=had_date)
    return task


def reschedule_overdue(user: User) -> int:
    """Set the due date to today on every open task that is overdue, keeping the times. Returns
    how many changed."""
    today = user_today(user)
    with transaction.atomic():
        lock_account(user.pk)  # it writes many task rows in one statement: take turns with the rest
        overdue = dates.overdue_set(user)
        # Their reminders follow the new date, re-armed when it is ahead (the user asked for this):
        # loaded in one query before the move and rewritten in one `bulk_update` after it.
        pending = reminders.reminders_of_tasks(overdue)
        count = overdue.update(due_date=today, today_position=0, updated_at=timezone.now())
        reminders.move_reminders(pending, user, today)
    return count


# -- Trash ---------------------------------------------------------------------------------------


def trash_task(task: Task) -> Task:
    """Move the task, and its subtasks not already in the Trash, to the Trash (all at one
    instant, which is how `restore_task` finds them again). Nothing is refunded."""
    if task.is_trashed:
        return task
    now = timezone.now()
    with transaction.atomic():
        # Account, the task, its subtasks, then what it blocks (`blockers.ended`).
        lock_account(task.user_id)
        lock_tasks(task.pk)
        subtask_ids = list(Task.objects.filter(parent=task).values_list('pk', flat=True))
        if subtask_ids:
            lock_tasks(*subtask_ids)
        # The blockers among them that are open stop blocking what they block (read before the write).
        was_open = blockers.open_ids([task.pk, *subtask_ids])
        won = Task.objects.filter(pk=task.pk, deleted_at__isnull=True).update(
            deleted_at=now, updated_at=now
        )
        if not won:
            task.refresh_from_db()
            return task
        Task.objects.filter(parent=task, deleted_at__isnull=True).update(
            deleted_at=now, updated_at=now
        )
        blockers.ended(was_open, now)
    task.deleted_at = now
    task.updated_at = now
    return task


def restore_task(task: Task) -> Task:
    """Take the task out of the Trash, with the subtasks trashed at the same instant. Refused while
    its parent is still trashed."""
    if not task.is_trashed:
        return task
    if (
        task.parent_id is not None
        and Task.objects.filter(pk=task.parent_id, deleted_at__isnull=False).exists()
    ):
        raise _error(NON_FIELD_ERRORS, 'Restore the parent task first.')
    now = timezone.now()
    trashed_at = task.deleted_at
    with transaction.atomic():
        lock_account(task.user_id)
        lock_tasks(task.pk)
        subtasks = Task.objects.filter(parent=task, deleted_at=trashed_at)
        subtask_ids = list(subtasks.values_list('pk', flat=True))
        if subtask_ids:
            lock_tasks(*subtask_ids)
        won = Task.objects.filter(pk=task.pk, deleted_at=trashed_at).update(
            deleted_at=None, updated_at=now
        )
        if won:
            restored = [task.pk, *subtask_ids]
            subtasks.update(deleted_at=None, updated_at=now)
            # An open task that is back blocks again only in the sense of a notice: what it blocks
            # keeps its state (`services.blockers`).
            blockers.reopened(
                Task.objects.filter(pk__in=restored, completed_at__isnull=True).values_list(
                    'pk', flat=True
                )
            )
    task.refresh_from_db()
    return task


def owes_credits(task: Task) -> bool:
    """Whether the task, or one of its subtasks, is still unpaid (`unpaid_at`, see
    `services.billing`)."""
    return Task.objects.filter(Q(pk=task.pk) | Q(parent=task), unpaid_at__isnull=False).exists()


def delete_forever(task: Task) -> None:
    """Delete the task's rows now (its subtasks go with it). Nothing is refunded.

    **An unpaid task cannot be deleted forever** (nor can the Trash purge it): deleting the debt
    would let the user top up 1 credit, have `billing.settle` find nothing owed and unblock
    creation. It is refused until a top-up settles it. Trashing and restoring stay allowed.
    """
    if owes_credits(task):
        raise _error(NON_FIELD_ERRORS, UNPAID_MESSAGE)
    try:
        _delete_with_attachments(task.pk)
    except attachments.STORAGE_ERRORS:
        logger.exception('Could not delete the files of task %s', task.pk)
        raise _error(NON_FIELD_ERRORS, FILES_NOT_DELETED_MESSAGE) from None


def _delete_with_attachments(task_id: UUID) -> int:
    """Delete the task, its subtasks and all their attachments (objects first, then rows, with
    the storage billing kept in step). A storage error raises before anything is deleted from the
    database and leaves the task for the next try. Returns how many task rows were removed."""
    user_id = Task.objects.filter(pk=task_id).values_list('user_id', flat=True).first()
    if user_id is None:
        return 0
    with transaction.atomic():
        # Account, the task, its subtasks, then what it blocks (`blockers.released` below).
        lock_account(user_id)
        lock_tasks(task_id)
        subtask_ids = list(Task.objects.filter(parent_id=task_id).values_list('pk', flat=True))
        if subtask_ids:
            lock_tasks(*subtask_ids)
        ids = [task_id, *subtask_ids]
        attachments.delete_for_tasks(ids)
        # Normally in the Trash already (nothing left to release); one that is deleted while open
        # frees what it blocked. Its dependents are found before the links go with it.
        dependents = blockers.dependents_of(blockers.open_ids(ids))
        # A parent's delete takes its subtasks too, so this counts the rows it removed.
        deleted = Task.objects.filter(pk=task_id).delete()[0]
        blockers.released(set(dependents) - set(ids))
        return deleted


def purge_trash() -> int:
    """Delete the tasks trashed more than `TodoSettings.trash_days` ago (the daily job). One bad
    row is logged and skipped. An unpaid task, or a parent with an unpaid subtask, is skipped too
    until it is settled (see `delete_forever`). Returns how many tasks were deleted."""
    cutoff = timezone.now() - timedelta(days=TodoSettings.load().trash_days)
    deleted = 0
    failed: set[UUID] = set()
    while True:
        batch = list(
            Task.objects.filter(deleted_at__lt=cutoff, unpaid_at__isnull=True)
            .exclude(subtasks__unpaid_at__isnull=False)
            .exclude(pk__in=failed)
            .order_by('deleted_at')
            .values_list('pk', flat=True)[:200]
        )
        if not batch:
            return deleted
        for pk in batch:
            try:
                deleted += _delete_with_attachments(pk)
            except Exception:
                failed.add(pk)
                logger.exception('Could not purge task %s', pk)


# -- Manual order --------------------------------------------------------------------------------


def reorder(user: User, *, scope: str, ids: list[UUID], parent: Task | None = None) -> int:
    """Write `position = index` for the given ids, inside one transaction, ignoring ids that are
    not in the set. `scope='today'` orders the tasks due today (`dates.todays_tasks`, the list the
    page orders; the overdue ones keep the default sort) in `Task.today_position`; `'subtasks'`
    orders the children of `parent` in `Task.position`. The two are separate columns, so ordering
    one list never puts the other into manual order. Returns how many tasks were positioned."""
    if scope == 'today':
        allowed = dates.todays_tasks(user)
        field = 'today_position'
    elif scope == 'subtasks':
        if parent is None or parent.user_id != user.pk:
            raise _error('parent', 'Choose the task whose subtasks to order.')
        allowed = dates.subtasks_of(parent).filter(user=user)
        field = 'position'
    else:
        raise _error('scope', 'Choose what to order.')
    in_set = {task.pk: task for task in allowed.filter(pk__in=ids)}
    now = timezone.now()
    changed: list[Task] = []
    for index, pk in enumerate(ids):
        task = in_set.get(pk)
        if task is not None:
            setattr(task, field, index)
            task.updated_at = now
            changed.append(task)
    with transaction.atomic():
        lock_account(user.pk)  # many task rows in one write: take turns with the rest
        Task.objects.bulk_update(changed, [field, 'updated_at'])
    return len(changed)
