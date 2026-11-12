"""Blockers: "Blocked by" links between a user's tasks, and what they do to states (issue #273).

A `TaskBlocker` row says one task blocks another, for good: it is kept when either task closes,
and only goes with a task that is deleted (the Trash emptied, the user). Both tasks are the same
user's, a task never blocks itself and the links never form a cycle, however long (`add_blocker`).
Subtasks block and are blocked like any task.

**The Blocked state.** It is the fourth open state and is muted like On hold (dimmed, never
overdue, its reminders held). It is not chosen: adding an *open* blocker (open and not in the
Trash) to an open task sets it Blocked, from any open state, and reopening a task puts it back to
Blocked when one of its blockers is still open (`tasks.uncomplete_task`). A blocker that is closed
(either resolution) or trashed when it is added is linked without changing the state, and so is a
closed task given a blocker.

**Auto-unblock.** When a blocker stops being open -- closed with either resolution (also through a
parent closing its subtasks, the phone's Complete button, the API, a repeating task), moved to the
Trash or deleted -- `released` looks at every task it blocks, under their row locks (two blockers
closing at once: the last to commit sees all). A task none of whose blockers is open any more that
is in the Blocked state goes back to To do (the reminders it held are delivered, as when leaving On
hold), and every such task that is open and not in the Trash -- whatever its state, so one the user
put On hold by hand keeps that state -- gets an in-app notice. A trashed one only has its state
corrected, silently, so it is not restored as Blocked with nothing to wait for. Removing a blocker
by hand does the same state change and sends no notice (the user did it).

**A blocker coming back** (reopened, `reopened`; restored from the Trash, `tasks.restore_task`)
does *not* block its tasks again: they only get a notice, "#1200 was reopened and blocks #1234",
and keep their state.

Closing a task that still has open blockers is allowed (after a confirmation on the web, which asks
for the open blockers by name: `open_blockers`); the links stay. When the close also closes the
open subtasks, the confirmation names the ones that have open blockers of their own
(`subtasks_with_open_blockers`). The next occurrence of a repeating task starts with none
(`recurrence` creates it through `create_task`, which gets no blockers).

**Locking.** Everything that writes the blocker graph -- closing, trashing, restoring, reopening and
deleting a task, adding and removing a link, creating a task with blockers, and the other writes
that lock more than one task (moving a task under a parent, a state change, `reorder`, rescheduling
the overdue ones, settling unpaid tasks) -- runs in a transaction whose **first** lock is the
user's `TodoAccount` row (`accounts.lock_account`), the one `accounts.allocate_number` takes too.
So one user's graph writes take turns, and the rows they then lock can't be taken in conflicting
orders (two tasks closing at once, a link added while its blocker closes: each pair of the four
deadlocks the review of #278 found needed two such writers at once). After the account, the order
is: the task acted on (the closing one), then its subtasks, then the tasks it blocks (the
dependents, in pk order, `released`). Nothing else locks the account while holding a task. The
same lock is what makes the no-cycle rule hold for any number of links added at once.

**The activity log** (issue #274, `services.events`) records, on the task that is blocked or freed:
a blocker added or removed by the user (`blocker_added` / `blocker_removed`) and, as separate entries,
the automatic state change that came of it (`blocked`, `unblocked`, which names the blockers that
closed, were trashed or deleted). Inserts only, in the transactions above, with no lock or
foreign key constraint of their own (see `services.events`).
"""

import logging
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.markdown import normalize_for_search
from apps.core.services import notifications

from ..limits import MAX_BLOCKERS_PER_TASK
from ..models import EventKind, Task, TaskBlocker, TaskState
from . import events
from .accounts import lock_account
from .task_state import lock_tasks, write_state

logger = logging.getLogger(__name__)

#: One number in a "Blocked by" box: `1200` or `#1200`, 1 to 9 figures like a task reference.
NUMBER_PATTERN = re.compile(r'#?([0-9]{1,9})')
#: How much of a task's title a notice quotes (the notification's own title limit is 140).
NOTICE_TITLE_CHARS = 60
UNBLOCKED_KEY = 'todo_unblocked'
REOPENED_KEY = 'todo_blocker_reopened'

NUMBERS_MESSAGE = 'Enter task numbers, like 1200, 1201.'
SELF_MESSAGE = 'A task cannot block itself.'
TOO_MANY_MESSAGE = f'A task can have at most {MAX_BLOCKERS_PER_TASK} blockers.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


def _quote(task: Task) -> str:
    """`#1234 Title` with the title cut short, for a notice or a message."""
    title = ' '.join(task.title.split())
    if len(title) > NOTICE_TITLE_CHARS:
        title = f'{title[: NOTICE_TITLE_CHARS - 1].rstrip()}…'
    return f'#{task.number} {title}'


# -- Reading -------------------------------------------------------------------------------------


def open_blockers(task: Task) -> list[Task]:
    """The task's blockers that are open and not in the Trash, by number: what keeps it Blocked and
    what closing it asks about. (A list of tasks reads them prefetched: `Task.open_blockers`.)"""
    return task.open_blockers


def has_open_blocker(task_id: UUID) -> bool:
    """Whether any blocker of the task is open and not in the Trash. One query."""
    return TaskBlocker.objects.filter(
        blocked_id=task_id, blocker__completed_at__isnull=True, blocker__deleted_at__isnull=True
    ).exists()


def open_blocker_ids(task_id: UUID) -> set[UUID]:
    """The ids of the task's blockers that are open and not in the Trash. One query."""
    return set(
        TaskBlocker.objects.filter(
            blocked_id=task_id,
            blocker__completed_at__isnull=True,
            blocker__deleted_at__isnull=True,
        ).values_list('blocker_id', flat=True)
    )


def blockers_of(task: Task) -> list[Task]:
    """Every blocker of the task, closed and trashed ones too (the links are kept), by number."""
    return [
        link.blocker
        for link in TaskBlocker.objects.filter(blocked=task)
        .select_related('blocker')
        .order_by('blocker__number')
    ]


def blocked_by(task: Task) -> list[Task]:
    """Every task `task` blocks, closed and trashed ones too, by number."""
    return [
        link.blocked
        for link in TaskBlocker.objects.filter(blocker=task)
        .select_related('blocked')
        .order_by('blocked__number')
    ]


def dependents_of(ids: Iterable[UUID]) -> list[UUID]:
    """The ids of the tasks blocked by any of `ids`."""
    return list(
        TaskBlocker.objects.filter(blocker_id__in=list(ids))
        .values_list('blocked_id', flat=True)
        .distinct()
    )


def subtasks_with_open_blockers(task: Task) -> list[tuple[Task, list[Task]]]:
    """The open, non-trashed subtasks of `task` that still have an open blocker of their own once
    the close that closes them with `task` is done, each with those blockers (by number): what the
    close dialog names when the person chose to close the subtasks. A blocker that closes in the
    same cascade (`task` itself, a sibling subtask) is not "still" open and is left out."""
    subtasks = list(
        Task.objects.filter(
            parent=task, completed_at__isnull=True, deleted_at__isnull=True
        ).order_by('number')
    )
    closing = {task.pk, *(sub.pk for sub in subtasks)}
    links = (
        TaskBlocker.objects.filter(
            blocked__in=subtasks,
            blocker__completed_at__isnull=True,
            blocker__deleted_at__isnull=True,
        )
        .exclude(blocker_id__in=closing)
        .select_related('blocker')
        .order_by('blocker__number')
    )
    by_subtask: dict[UUID, list[Task]] = {}
    for link in links:
        by_subtask.setdefault(link.blocked_id, []).append(link.blocker)
    return [(sub, by_subtask[sub.pk]) for sub in subtasks if sub.pk in by_subtask]


def parse_numbers(text: str, field: str = 'blocked_by') -> list[int]:
    """The task numbers in a "Blocked by" box (`1200, 1201` or `#1200 #1201`: commas, semicolons
    and spaces separate them), once each, in order. Anything else is a refusal."""
    numbers: list[int] = []
    for token in re.split(r'[\s,;]+', (text or '').strip()):
        if not token:
            continue
        match = NUMBER_PATTERN.fullmatch(token)
        if match is None:
            raise _error(field, NUMBERS_MESSAGE)
        number = int(match.group(1))
        if number not in numbers:
            numbers.append(number)
    return numbers


def tasks_by_numbers(user: User, numbers: Sequence[int], field: str = 'blocked_by') -> list[Task]:
    """The user's own tasks numbered `numbers` that can be a blocker (not in the Trash), in that
    order; one that is not theirs or does not exist is "There is no task #N." (the same words, so a
    number can't be probed), one in the Trash is named as such."""
    found = {task.number: task for task in Task.objects.filter(user=user, number__in=numbers)}
    tasks = []
    for number in numbers:
        task = found.get(number)
        if task is None:
            raise _error(field, f'There is no task #{number}.')
        if task.is_trashed:
            raise _error(field, f'#{number} is in the trash. Restore it first.')
        tasks.append(task)
    return tasks


#: What the task page's "Blocked by" box may hold: a number (`1200`, `#1200`, also followed by the
#: title a suggestion showed) or words from a title.
_LEADING_NUMBER = re.compile(r'(?:#([0-9]{1,9})(?:\s|$)|([0-9]{1,9})$)')
SUGGESTIONS = 10
CHOOSE_MESSAGE = 'Choose a task from the suggestions, or type its number.'


def find_blocker(user: User, text: str, task: Task) -> Task:
    """The task a "Blocked by" box on `task`'s page names: by number (`1200`, `#1200`, or `#1200`
    followed by anything, as a suggestion may fill it in), else by its title -- the one open task other
    than `task` whose title is exactly these words, or contains them and no other does. Anything
    else is a refusal (`blocker`)."""
    text = (text or '').strip()
    match = _LEADING_NUMBER.match(text)
    if match is not None:
        return tasks_by_numbers(user, [int(match.group(1) or match.group(2))], 'blocker')[0]
    words = normalize_for_search(text)
    if not words:
        raise _error('blocker', 'Enter the number of a task.')
    candidates = Task.objects.filter(
        user=user, completed_at__isnull=True, deleted_at__isnull=True
    ).exclude(pk=task.pk)
    exact = list(candidates.filter(title_text=words)[:2])
    if len(exact) == 1:
        return exact[0]
    found = list(candidates.filter(title_text__contains=words)[:2]) if not exact else exact
    if len(found) == 1:
        return found[0]
    raise _error('blocker', CHOOSE_MESSAGE)


def dependents_closure(task_id: UUID) -> set[UUID]:
    """Every task reachable from `task_id` through "Blocks": the tasks it blocks, those they block,
    and so on. Making any of them block `task_id` would close a loop. Walked a level (one query) at
    a time, like `_would_cycle`."""
    seen: set[UUID] = set()
    frontier = [task_id]
    while frontier:
        found = set(
            TaskBlocker.objects.filter(blocker_id__in=frontier).values_list('blocked_id', flat=True)
        )
        frontier = [pk for pk in found if pk not in seen]
        seen.update(frontier)
    return seen


def suggestions(user: User, task: Task, text: str) -> list[Task]:
    """The open tasks to offer as blockers of `task` for what has been typed: not itself, not one it
    already has, and not one that would be refused as a cycle (any task `task` blocks, directly or
    through other tasks), whose number starts with the digits typed or whose title contains the
    words (all of the open tasks, newest numbers last, when nothing is typed). At most
    `SUGGESTIONS`."""
    queryset = (
        Task.objects.filter(user=user, completed_at__isnull=True, deleted_at__isnull=True)
        .exclude(pk=task.pk)
        .exclude(blocks_links__blocked=task)
        .exclude(pk__in=dependents_closure(task.pk))
    )
    text = (text or '').strip()
    digits = text.lstrip('#')
    if digits.isascii() and digits.isdigit():
        queryset = queryset.filter(number__startswith=digits)
    elif text:
        queryset = queryset.filter(title_text__contains=normalize_for_search(text))
    return list(queryset.order_by('number')[:SUGGESTIONS])


def clean_new(user: User, numbers: Iterable[int]) -> list[Task]:
    """The blockers of a task about to be created (`create_task(blocked_by=...)`): the user's own,
    not in the Trash, at most `MAX_BLOCKERS_PER_TASK`."""
    wanted = list(dict.fromkeys(numbers))
    if len(wanted) > MAX_BLOCKERS_PER_TASK:
        raise _error('blocked_by', TOO_MANY_MESSAGE)
    return tasks_by_numbers(user, wanted)


# -- Adding and removing -------------------------------------------------------------------------


def _would_cycle(task_id: UUID, blocker_id: UUID) -> bool:
    """Whether making `blocker_id` block `task_id` closes a loop: `blocker_id` is already blocked by
    `task_id`, directly or through any number of other tasks. Walks the "blocked by" links from the
    blocker, a level (one query) at a time."""
    seen = {blocker_id}
    frontier = [blocker_id]
    while frontier:
        parents = set(
            TaskBlocker.objects.filter(blocked_id__in=frontier).values_list('blocker_id', flat=True)
        )
        if task_id in parents:
            return True
        frontier = [pk for pk in parents if pk not in seen]
        seen.update(frontier)
    return False


def _check_add(current: Task, other: Task) -> bool:
    """What adding "`other` blocks `current`" would refuse, on the rows as given: a task of another
    user, itself, a task in the Trash (either), too many blockers, a cycle. Returns False when the
    link already exists (nothing to do), else True. No writes."""
    if other.user_id != current.user_id:
        raise _error('blocker', 'Unknown task.')
    if other.pk == current.pk:
        raise _error('blocker', SELF_MESSAGE)
    if current.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
    if other.is_trashed:
        raise _error('blocker', f'#{other.number} is in the trash. Restore it first.')
    if TaskBlocker.objects.filter(blocked=current, blocker=other).exists():
        return False
    if TaskBlocker.objects.filter(blocked=current).count() >= MAX_BLOCKERS_PER_TASK:
        raise _error('blocker', TOO_MANY_MESSAGE)
    if _would_cycle(current.pk, other.pk):
        raise _error(
            'blocker',
            f'#{other.number} is already blocked by #{current.number}, '
            'directly or through other tasks.',
        )
    return True


def check_add(task: Task, blocker: Task) -> bool:
    """What `add_blocker` would refuse that can be told without writing anything (see
    `_check_add`), for a caller that wants to refuse before spending a rate-limit budget. It reads
    the rows as the caller loaded them and takes no lock: `add_blocker` still decides for good.
    Returns whether a link would be made (False: it already exists, nothing to do)."""
    return _check_add(task, blocker)


def add_blocker(task: Task, blocker: Task) -> bool:
    """Make `blocker` block `task`. Returns whether a link was made (False: it already was).

    Both are the same user's and out of the Trash, `blocker` is another task and no cycle comes of
    it (`'#1200 is already blocked by #1234, directly or through other tasks.'`); a task has at
    most `MAX_BLOCKERS_PER_TASK`. An open blocker sets an open `task` to Blocked, from any open
    state; a closed blocker only links, and so does a closed `task`. The user's account row is
    locked first and both tasks after it (`lock_account`, see the module docstring), so links added
    at once -- between the same two tasks or any others, three in a ring included -- take turns,
    each seeing the others' committed links: no cycle of any length can be committed.
    """
    if blocker.user_id != task.user_id:
        raise _error('blocker', 'Unknown task.')
    if blocker.pk == task.pk:
        raise _error('blocker', SELF_MESSAGE)
    now = timezone.now()
    with transaction.atomic():
        lock_account(task.user_id)
        locked = lock_tasks(task.pk, blocker.pk)
        current, other = locked.get(task.pk), locked.get(blocker.pk)
        if current is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        if other is None:
            raise _error('blocker', 'Unknown task.')
        if not _check_add(current, other):
            return False
        try:
            with transaction.atomic():
                TaskBlocker.objects.create(blocker=other, blocked=current)
        except IntegrityError:  # a racing identical add won
            return False
        entries = [events.entry(current, EventKind.BLOCKER_ADDED, events.task_ref(other))]
        if current.completed_at is None and other.completed_at is None:
            previous = current.state
            if write_state(current, TaskState.BLOCKED, now):
                entries.append(_blocked_entry(current, previous, [other]))
        events.record_many(entries)
    task.state = current.state
    task.today_position = current.today_position
    task.updated_at = current.updated_at
    return True


def _blocked_entry(task: Task, previous: str, blockers: Sequence[Task]) -> events.Entry:
    """The automatic entry of a task that became Blocked."""
    return events.entry(
        task,
        EventKind.BLOCKED,
        {'from': previous, 'blockers': [events.task_ref(other) for other in blockers]},
        automatic=True,
    )


def link_new_task(task: Task, blockers: Sequence[Task]) -> None:
    """Link a task `create_task` just made to its `blockers` (validated by `clean_new`) and set it
    Blocked when one of them is open (decided from the database, in the creating transaction). A new
    task has nothing blocked by it, so no cycle is possible. Runs after the account lock
    `create_task` took (`allocate_number`); the blockers' rows are locked first, so a blocker
    closing at the same moment has committed (and is seen closed) or waits and then sees the new
    link and releases the task. The new task is invisible to everyone else, so locking the blockers
    after the account can't deadlock."""
    if not blockers:
        return
    lock_tasks(*(other.pk for other in blockers))
    TaskBlocker.objects.bulk_create(
        [TaskBlocker(blocker=other, blocked=task) for other in blockers], ignore_conflicts=True
    )
    entries = [
        events.entry(task, EventKind.BLOCKER_ADDED, events.task_ref(other)) for other in blockers
    ]
    open_ids = open_blocker_ids(task.pk)  # the one query that decides, and names them
    if open_ids:
        previous = task.state
        task.state = TaskState.BLOCKED
        Task.objects.filter(pk=task.pk).update(state=TaskState.BLOCKED)
        open_ones = [other for other in blockers if other.pk in open_ids]
        entries.append(_blocked_entry(task, previous, open_ones))
    events.record_many(entries)


def remove_blocker(task: Task, blocker: Task) -> bool:
    """Take `blocker` off `task`; returns whether there was such a link (no link: nothing to do).
    A Blocked `task` left with no open blocker goes back to To do, quietly (the user did it)."""
    now = timezone.now()
    with transaction.atomic():
        lock_account(task.user_id)
        current = lock_tasks(task.pk).get(task.pk)
        if current is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        if current.is_trashed:
            raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
        removed = TaskBlocker.objects.filter(blocked=current, blocker=blocker).delete()[0]
        if not removed:
            return False
        entries = [events.entry(current, EventKind.BLOCKER_REMOVED, events.task_ref(blocker))]
        if (
            current.completed_at is None
            and current.state == TaskState.BLOCKED
            and not has_open_blocker(current.pk)
        ):
            write_state(current, TaskState.TODO, now)
            entries.append(
                _unblocked_entry(current, [events.task_ref(blocker) | {'reason': 'removed'}])
            )
        events.record_many(entries)
    task.state = current.state
    task.updated_at = current.updated_at
    return True


# -- What a blocker ending or coming back does ---------------------------------------------------


def _notice(user_id: UUID, key: str, title: str, body: str, number: int) -> None:
    """One in-app notice per `key`, replacing the last. Its own savepoint, never raises: telling the
    user must not undo or fail the close that caused it."""
    try:
        with transaction.atomic():
            notifications.delete_for_key(user_id, key, source='todo')
            notifications.notify(
                user_id,
                source='todo',
                title=title,
                body=body,
                url=reverse('todo:task', args=[number]),
                key=key,
            )
    except Exception:
        logger.exception('Could not write the notice %s', key)


def _unblocked_entry(task: Task, blockers: list[dict]) -> events.Entry:
    """The automatic entry of a task that is no longer Blocked: `blockers` are `{number, title,
    reason}` (reason `closed`, `trashed`, `deleted` or `removed`)."""
    return events.entry(task, EventKind.UNBLOCKED, {'blockers': blockers}, automatic=True)


def causes_of(blocker_ids: Iterable[UUID], reason: str) -> dict[UUID, list[dict]]:
    """For each task the blockers `blocker_ids` block, those of them that block it, as `{number,
    title, reason}`: what `released` writes in the log of a task it frees. One query. Read it
    *before* the links go (a task deleted for good takes its links with it)."""
    ids = list(blocker_ids)
    found: dict[UUID, list[dict]] = {}
    if not ids:
        return found
    links = (
        TaskBlocker.objects.filter(blocker_id__in=ids)
        .select_related('blocker')
        .order_by('blocker__number')
    )
    for link in links:
        found.setdefault(link.blocked_id, []).append(
            events.task_ref(link.blocker) | {'reason': reason}
        )
    return found


def released(
    dependent_ids: Iterable[UUID],
    now: datetime | None = None,
    causes: dict[UUID, list[dict]] | None = None,
) -> int:
    """The blockers of these tasks stopped being open (closed, trashed or deleted: `ended` finds the
    dependents, a delete finds them before the rows go): every one with no open blocker left that
    is open and not in the Trash is told, and a Blocked one goes back to To do. Returns how many
    were told. Call it in the transaction that ended the blockers, which holds the account lock
    (`lock_account`): the dependents are locked here, in pk order, after the task that closed and its
    subtasks. However many tasks the blocker blocks, which of them still have an open blocker is
    one query over all the locked rows. `causes` (`causes_of`) names, for the log entry of a task that
    goes back to To do, the blockers that ended."""
    ids = list(dict.fromkeys(dependent_ids))
    if not ids:
        return 0
    now = now or timezone.now()
    told = 0
    causes = causes or {}
    locked = lock_tasks(*ids)
    entries: list[events.Entry] = []
    still_blocked = set(
        TaskBlocker.objects.filter(
            blocked_id__in=list(locked),
            blocker__completed_at__isnull=True,
            blocker__deleted_at__isnull=True,
        ).values_list('blocked_id', flat=True)
    )
    for dependent in locked.values():
        if dependent.completed_at is not None or dependent.pk in still_blocked:
            continue
        if dependent.state == TaskState.BLOCKED:
            write_state(dependent, TaskState.TODO, now, release=not dependent.is_trashed)
            entries.append(_unblocked_entry(dependent, causes.get(dependent.pk, [])))
        if dependent.is_trashed:
            continue
        _notice(
            dependent.user_id,
            f'{UNBLOCKED_KEY}:{dependent.pk}',
            f'{_quote(dependent)} is no longer blocked',
            'Everything that was blocking it is closed.',
            dependent.number,
        )
        told += 1
    events.record_many(entries)
    return told


def ended(blocker_ids: Iterable[UUID], now: datetime | None = None, reason: str = 'closed') -> int:
    """`blocker_ids` were open and are not any more (`reason`: `closed` or `trashed`, for the log):
    `released` for the tasks they block."""
    ids = list(blocker_ids)
    if not ids:
        return 0
    return released(dependents_of(ids), now, causes_of(ids, reason))


def reopened(blocker_ids: Iterable[UUID]) -> int:
    """`blocker_ids` are open blockers again (reopened, or restored from the Trash): the open tasks
    they block get a notice and keep their state -- a blocker coming back does not block them again.
    Returns how many were told."""
    ids = list(blocker_ids)
    if not ids:
        return 0
    links = (
        TaskBlocker.objects.filter(
            blocker_id__in=ids, blocked__completed_at__isnull=True, blocked__deleted_at__isnull=True
        )
        .select_related('blocker', 'blocked')
        .order_by('blocked__number', 'blocker__number')
    )
    told = 0
    for link in links:
        _notice(
            link.blocked.user_id,
            f'{REOPENED_KEY}:{link.blocked_id}',
            f'#{link.blocker.number} was reopened and blocks #{link.blocked.number}',
            f'{_quote(link.blocker)} is open again. {_quote(link.blocked)} keeps its state.',
            link.blocked.number,
        )
        told += 1
    return told


def open_ids(ids: Iterable[UUID]) -> list[UUID]:
    """Of `ids`, the tasks that are open and not in the Trash right now: read *before* closing or
    trashing them, they are the blockers that stop blocking."""
    return list(
        Task.objects.filter(
            pk__in=list(ids), completed_at__isnull=True, deleted_at__isnull=True
        ).values_list('pk', flat=True)
    )
