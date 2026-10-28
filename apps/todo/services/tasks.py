"""Creating, changing, completing, postponing, trashing and ordering tasks (issue #178).

Every rule lives here, so the web views (and later the API) enforce the same ones; a refusal is a
`django.core.exceptions.ValidationError` keyed by field (`'__all__'` for one that belongs to no
field), and `InsufficientCreditsError` (`apps.billing.services`) for a task the balance can't
cover. Trashing, completing and the like never refund what a task cost.
"""

import logging
from collections.abc import Iterable, Mapping
from datetime import date, time, timedelta
from typing import Any
from uuid import UUID

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today
from apps.core.markdown import toggle_checklist_item

from ..models import Label, Priority, Recurrence, RecurrenceMode, Task, TaskSource, TodoSettings
from . import attachments, dates, reminders
from . import recurrence as recurrence_service
from .accounts import allocate_number, get_account
from .billing import charge_task_creation
from .limits import DESCRIPTION_MAX, TITLE_MAX
from .references import sync_references

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

POSTPONE_PRESETS = ('tomorrow', 'next_week', 'next_weekend', 'date')
REORDER_SCOPES = ('today', 'subtasks')

UNPAID_MESSAGE = (
    'This task has not been paid for yet. Top up your credits first: it can be deleted forever '
    'once it is settled.'
)
FILES_NOT_DELETED_MESSAGE = "The task's files could not be deleted right now. Try again later."
BLOCKED_MESSAGE = 'Top up your credits to add tasks: a repeating task is waiting to be paid.'
TOO_MANY_MESSAGE = 'Too many open tasks. Complete or delete some to add more.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


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


def _lock_tasks(*pks: UUID) -> dict[UUID, Task]:
    """The tasks `pks`, re-read with their rows locked (in pk order, so two callers locking the same
    rows can't deadlock). Call it inside a transaction; a task that is gone is missing from the
    result."""
    return {
        task.pk: task for task in Task.objects.select_for_update().filter(pk__in=pks).order_by('pk')
    }


def _locked_parent(parent: Task) -> Task:
    """`parent` re-read under its row lock (inside a transaction), or a refusal if it is gone."""
    locked = _lock_tasks(parent.pk).get(parent.pk)
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


def _after_the_others(queryset, field: str) -> int:
    """`max(field) + 1` over `queryset` when it has a manual order (any value above 0), else 0 (the
    default sort applies). `queryset` is the very set the list orders (`dates.has_manual_order`)."""
    top = queryset.aggregate(top=Max(field))['top'] or 0
    return top + 1 if top > 0 else 0


def next_position(parent: Task | None) -> int:
    """`Task.position` of a task that joins `parent`'s subtasks: after the others when they have a
    manual order, else 0. Only subtasks have a manual order in `position`; a top-level task is
    ordered in Today by `today_position` (`next_today_position`) and keeps 0 here."""
    if parent is None:
        return 0
    return _after_the_others(dates.subtasks_of(parent), 'position')


def next_today_position(user: User, due_date: date | None) -> int:
    """`Task.today_position` of a new task: after the others only when it lands in the Today list
    (it is due today) and *that list* already has a manual order; else 0, so a task created for
    another day, or in a list nobody reordered, never changes how Today sorts."""
    if due_date is None or due_date != user_today(user):
        return 0
    return _after_the_others(dates.todays_tasks(user), 'today_position')


# -- Create and update ---------------------------------------------------------------------------


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
    source: str = TaskSource.WEB,
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
    """
    cleaned_title = clean_title(title)
    cleaned_description = clean_description(description)
    cleaned_priority = clean_priority(priority)
    cleaned_date, cleaned_time = clean_due(due_date, due_time)
    cleaned_labels = clean_labels(user, labels)
    cleaned_parent = clean_parent(user, parent)
    cleaned_rule = recurrence_service.clean_rule(rrule, user_today(user)) if rrule else ''
    cleaned_mode = recurrence_service.clean_mode(recurrence_mode)
    if (cleaned_rule or recurrence is not None) and cleaned_parent is not None:
        raise _error('rrule', 'A subtask cannot repeat.')
    if recurrence is not None and (cleaned_rule or recurrence.user_id != user.pk):
        raise _error('rrule', 'Unknown repeat rule.')
    if cleaned_rule and cleaned_date is None:
        cleaned_date = recurrence_service.first_date(cleaned_rule, user_today(user))

    settings = TodoSettings.load()
    account = get_account(user)
    with transaction.atomic():
        number = allocate_number(account)  # locks the account row; refreshes `account`
        if account.creation_blocked_at is not None and not allow_unpaid:
            raise _error(NON_FIELD_ERRORS, BLOCKED_MESSAGE)
        if cleaned_parent is not None:
            # Locked and re-read: a concurrent move of the parent under another task (`update_task`)
            # would otherwise leave this a subtask of a subtask.
            cleaned_parent = clean_parent(user, _locked_parent(cleaned_parent))
        if (
            dates.open_tasks(user).count() >= settings.max_open_tasks_per_user
        ):  # under the lock, so racing creations can't pass the cap together
            raise _error(NON_FIELD_ERRORS, TOO_MANY_MESSAGE)
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
            source=source,
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
        reminders.apply_defaults(task, account)
    return task


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

    with transaction.atomic():
        # The task's row (and the new parent's, in pk order) is locked and re-read first: the
        # checks below read rows another request may be changing (a subtask being added to this
        # task, the new parent moving under something), and the description is compared with the
        # text stored *now*, so the references written always match the text that was saved.
        new_parent_pk = new_parent.pk if moves and new_parent is not None else None
        locked = _lock_tasks(*(pk for pk in (task.pk, new_parent_pk) if pk is not None))
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
        fresh = Task.objects.select_for_update().filter(pk=task.pk).first()
        if fresh is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        try:
            description = toggle_checklist_item(fresh.description, index, checked)
        except IndexError, ValueError:
            raise _error('index', 'No such checklist item.') from None
        return update_task(fresh, changes={'description': description})


# -- Completing ----------------------------------------------------------------------------------


def complete_task(task: Task) -> Task:
    """Complete the task, and its open subtasks in the same transaction. A completed task is left
    as it is. A repeating task creates its next occurrence in the same transaction, before the open
    subtasks are completed (they are copied to it: `services.recurrence.on_completed`)."""
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before completing it.')
    now = timezone.now()
    with transaction.atomic():
        won = Task.objects.filter(pk=task.pk, completed_at__isnull=True).update(
            completed_at=now, updated_at=now
        )
        if not won:
            task.refresh_from_db()
            return task
        recurrence_service.on_completed(task)
        Task.objects.filter(parent=task, completed_at__isnull=True, deleted_at__isnull=True).update(
            completed_at=now, updated_at=now
        )
    task.completed_at = now
    task.updated_at = now
    return task


def uncomplete_task(task: Task) -> Task:
    """Make a completed task open again. Its parent, if it has one, stays as it is."""
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
    now = timezone.now()
    Task.objects.filter(pk=task.pk, completed_at__isnull=False).update(
        completed_at=None, updated_at=now
    )
    task.refresh_from_db()
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
    account = get_account(user)
    with transaction.atomic():
        with_reminders = list(dates.overdue_set(user).filter(reminders__isnull=False).distinct())
        count = dates.overdue_set(user).update(
            due_date=today, today_position=0, updated_at=timezone.now()
        )
        for task in with_reminders:  # their reminders follow the new date
            task.user = user
            task.due_date = today
            reminders.reschedule(task, account)
    return count


# -- Trash ---------------------------------------------------------------------------------------


def trash_task(task: Task) -> Task:
    """Move the task, and its subtasks not already in the Trash, to the Trash (all at one
    instant, which is how `restore_task` finds them again). Nothing is refunded."""
    if task.is_trashed:
        return task
    now = timezone.now()
    with transaction.atomic():
        won = Task.objects.filter(pk=task.pk, deleted_at__isnull=True).update(
            deleted_at=now, updated_at=now
        )
        if not won:
            task.refresh_from_db()
            return task
        Task.objects.filter(parent=task, deleted_at__isnull=True).update(
            deleted_at=now, updated_at=now
        )
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
        won = Task.objects.filter(pk=task.pk, deleted_at=trashed_at).update(
            deleted_at=None, updated_at=now
        )
        if won:
            Task.objects.filter(parent=task, deleted_at=trashed_at).update(
                deleted_at=None, updated_at=now
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
    with transaction.atomic():
        ids = list(
            Task.objects.filter(Q(pk=task_id) | Q(parent_id=task_id)).values_list('pk', flat=True)
        )
        attachments.delete_for_tasks(ids)
        # A parent's delete takes its subtasks too, so this counts the rows it removed.
        return Task.objects.filter(pk=task_id).delete()[0]


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
        Task.objects.bulk_update(changed, [field, 'updated_at'])
    return len(changed)
