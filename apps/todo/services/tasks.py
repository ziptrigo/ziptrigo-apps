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
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today
from apps.core.markdown import toggle_checklist_item

from ..models import Label, Priority, Task, TaskSource, TodoSettings
from . import dates
from .accounts import allocate_number, get_account
from .billing import charge_task_creation
from .limits import DESCRIPTION_MAX, TITLE_MAX

logger = logging.getLogger(__name__)

#: The fields `update_task` accepts.
UPDATABLE = frozenset(
    {'title', 'description', 'priority', 'due_date', 'due_time', 'labels', 'parent'}
)

POSTPONE_PRESETS = ('tomorrow', 'next_week', 'next_weekend', 'date')
REORDER_SCOPES = ('today', 'subtasks')

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


def next_position(user: User, parent: Task | None) -> int:
    """`Task.position` of a task that joins `parent`'s subtasks (or the user's top-level tasks):
    after the others when a manual order exists, else 0 (the default sort applies)."""
    top = (
        Task.objects.filter(user=user, parent=parent, deleted_at__isnull=True).aggregate(
            top=Max('position')
        )['top']
        or 0
    )
    return top + 1 if top > 0 else 0


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
    """
    cleaned_title = clean_title(title)
    cleaned_description = clean_description(description)
    cleaned_priority = clean_priority(priority)
    cleaned_date, cleaned_time = clean_due(due_date, due_time)
    cleaned_labels = clean_labels(user, labels)
    cleaned_parent = clean_parent(user, parent)

    settings = TodoSettings.load()
    account = get_account(user)
    with transaction.atomic():
        number = allocate_number(account)  # locks the account row; refreshes `account`
        if account.creation_blocked_at is not None and not allow_unpaid:
            raise _error(NON_FIELD_ERRORS, BLOCKED_MESSAGE)
        if (
            dates.open_tasks(user).count() >= settings.max_open_tasks_per_user
        ):  # under the lock, so racing creations can't pass the cap together
            raise _error(NON_FIELD_ERRORS, TOO_MANY_MESSAGE)
        charged, unpaid = charge_task_creation(
            account, settings, number=number, allow_unpaid=allow_unpaid
        )
        task = Task(
            user=user,
            number=number,
            parent=cleaned_parent,
            title=cleaned_title,
            description=cleaned_description,
            priority=cleaned_priority,
            due_date=cleaned_date,
            due_time=cleaned_time,
            source=source,
            charged_credits=charged,
            unpaid_at=timezone.now() if unpaid else None,
            position=next_position(user, cleaned_parent),
        )
        task.save()
        if cleaned_labels:
            task.labels.set(cleaned_labels)
    return task


def update_task(task: Task, *, changes: Mapping[str, Any]) -> Task:
    """Apply a partial update (`UPDATABLE` keys only) with the same validation as `create_task`.

    Clearing `due_date` clears `due_time` too. A task moves under a `parent` only when it has no
    subtasks of its own; `parent=None` makes a subtask a top-level task. A trashed task is
    read-only.
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
    labels = clean_labels(task.user, changes['labels']) if 'labels' in changes else None
    if 'parent' in changes:
        parent = clean_parent(task.user, changes['parent'])
        if parent is not None:
            if parent.pk == task.pk:
                raise _error('parent', 'A task cannot be its own parent.')
            if Task.objects.filter(parent=task).exists():
                raise _error('parent', 'A task with subtasks cannot become a subtask.')
        if parent != task.parent:
            values['parent'] = parent
            values['position'] = next_position(task.user, parent)

    for name, value in values.items():
        setattr(task, name, value)
    with transaction.atomic():
        task.save(update_fields=[*values, 'updated_at'])
        if labels is not None:
            task.labels.set(labels)
    return task


def toggle_checklist(task: Task, index: int, checked: bool) -> Task:
    """Tick or untick the `index`th checklist item of the description (written back to the text)."""
    try:
        description = toggle_checklist_item(task.description, index, checked)
    except IndexError, ValueError:
        raise _error('index', 'No such checklist item.') from None
    return update_task(task, changes={'description': description})


# -- Completing ----------------------------------------------------------------------------------


def complete_task(task: Task) -> Task:
    """Complete the task, and its open subtasks in the same transaction. A completed task is left
    as it is. (The recurrence sub-issue hooks in here.)"""
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
    schedule (that is the recurrence sub-issue's rule; nothing here touches it)."""
    if not task.is_open:
        raise _error(NON_FIELD_ERRORS, 'Only open tasks can be postponed.')
    target = postpone_target(user_today(task.user), preset, date)
    task.due_date = target
    task.save(update_fields=['due_date', 'updated_at'])
    return task


def reschedule_overdue(user: User) -> int:
    """Set the due date to today on every open task that is overdue, keeping the times. Returns
    how many changed."""
    return dates.overdue_set(user).update(due_date=user_today(user), updated_at=timezone.now())


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


def delete_forever(task: Task) -> None:
    """Delete the task's rows now (its subtasks go with it). Nothing is refunded."""
    task.delete()


def purge_trash() -> int:
    """Delete the tasks trashed more than `TodoSettings.trash_days` ago (the daily job). One bad
    row is logged and skipped. Returns how many tasks were deleted."""
    cutoff = timezone.now() - timedelta(days=TodoSettings.load().trash_days)
    deleted = 0
    failed: set[UUID] = set()
    while True:
        batch = list(
            Task.objects.filter(deleted_at__lt=cutoff)
            .exclude(pk__in=failed)
            .order_by('deleted_at')
            .values_list('pk', flat=True)[:200]
        )
        if not batch:
            return deleted
        for pk in batch:
            try:
                # A parent's delete takes its subtasks too, so this counts the rows it removed.
                count, _ = Task.objects.filter(pk=pk).delete()
                deleted += count
            except Exception:
                failed.add(pk)
                logger.exception('Could not purge task %s', pk)


# -- Manual order --------------------------------------------------------------------------------


def reorder(user: User, *, scope: str, ids: list[UUID], parent: Task | None = None) -> int:
    """Write `position = index` for the given ids, inside one transaction, ignoring ids that are
    not in the set: `scope='today'` (the user's Today set) or `'subtasks'` (the children of
    `parent`). One `Task.position` column serves both, so a subtask that is also in Today is
    ordered by the same number in each. Returns how many tasks were positioned."""
    if scope == 'today':
        allowed = dates.today_set(user)
    elif scope == 'subtasks':
        if parent is None or parent.user_id != user.pk:
            raise _error('parent', 'Choose the task whose subtasks to order.')
        allowed = Task.objects.filter(user=user, parent=parent, deleted_at__isnull=True)
    else:
        raise _error('scope', 'Choose what to order.')
    in_set = {task.pk: task for task in allowed.filter(pk__in=ids)}
    now = timezone.now()
    changed: list[Task] = []
    for index, pk in enumerate(ids):
        task = in_set.get(pk)
        if task is not None:
            task.position = index
            task.updated_at = now
            changed.append(task)
    with transaction.atomic():
        Task.objects.bulk_update(changed, ['position', 'updated_at'])
    return len(changed)
