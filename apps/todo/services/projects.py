"""A user's projects (issue #314): a flat list that tasks are filed in.

A task is in at most one project, or in none. **A subtask is always in its parent's project**: the
column is kept equal on both (`move_task` moves the subtasks with their parent, `create_task` gives
a subtask its parent's, `update_task` gives a task moved under a parent that parent's), so a list
filters on `Task.project` alone.

**Archiving** (`archive_project`) is reversible and deletes nothing. While a project is archived its
open tasks are muted as a whole, like a task that is On hold: they are off Today, Upcoming and All
(`dates.not_archived`), never overdue (`Task.is_overdue`), and their reminders wait
(`reminders.deliver_due` and `sweep_stale` skip them). `unarchive_project` sends **one** reminder per
task for what it held (`reminders.release_held`), except for a task that is also On hold or Blocked,
whose reminders keep waiting for that. A task moved out of an archived project gets the same. Nothing
else about a task changes: a repeating task closed while archived still makes its next occurrence, in
the same project; a task it blocks stays Blocked.

**Deleting** a project (`delete_project`) removes only the project: its tasks are kept and are then
in no project.

Every write that touches tasks, counts projects or changes the archive takes the user's
`TodoAccount` row first (`accounts.lock_account`), like the other To-do writes that touch more than
one task (`services.blockers`' note on locking); renaming and reordering projects are single-table
writes and take none.
"""

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, QuerySet
from django.utils import timezone

from apps.accounts.models import User

from ..models import MUTED_STATES, EventKind, Project, Task, TaskBlocker, TodoSettings
from . import events, reminders
from .accounts import get_account, lock_account
from .labels import clean_color
from .limits import PROJECT_DESCRIPTION_MAX, PROJECT_NAME_MAX
from .task_state import lock_tasks

logger = logging.getLogger(__name__)

DUPLICATE_MESSAGE = 'You already have a project with this name.'
ARCHIVED_MESSAGE = 'This project is archived. Unarchive it to add tasks to it.'
UNKNOWN_MESSAGE = 'Unknown project.'
SUBTASK_MESSAGE = "A subtask is in its parent's project. Move the parent instead."
#: How many projects the sidebar lists before "and N more".
SIDEBAR_LIMIT = 20


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


# -- Validation ----------------------------------------------------------------------------------


def clean_name(name: object) -> str:
    cleaned = ' '.join(str(name or '').split())
    if not cleaned:
        raise _error('name', 'Name is required.')
    if len(cleaned) > PROJECT_NAME_MAX:
        raise _error('name', f'Name is too long (at most {PROJECT_NAME_MAX} characters).')
    return cleaned


def clean_description(description: object) -> str:
    # A browser submits `\r\n`, which would count twice against the cap.
    cleaned = str(description or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if len(cleaned) > PROJECT_DESCRIPTION_MAX:
        raise _error(
            'description',
            f'Description is too long (at most {PROJECT_DESCRIPTION_MAX:,} characters).',
        )
    return cleaned


def _check_unique(user: User, name: str, *, exclude: Project | None = None) -> None:
    taken = Project.objects.filter(user=user, name__iexact=name)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise _error('name', DUPLICATE_MESSAGE)


def clean_project(
    user: User, project: Project | str | UUID | None, *, allow_archived: bool = False
) -> Project | None:
    """`project` (a project, its id, or nothing) as one of the user's projects that a task may be
    put in, or `None` for "no project". Someone else's, an unknown id and a malformed one are all
    "Unknown project."; an archived one is refused unless `allow_archived` (the next occurrence of
    a repeating task stays in its project)."""
    if project is None or project == '':
        return None
    if not isinstance(project, Project):
        try:
            found = Project.objects.filter(user=user, pk=project).first()
        except ValueError, ValidationError:  # a malformed id
            found = None
        if found is None:
            raise _error('project', UNKNOWN_MESSAGE)
        project = found
    elif project.user_id != user.pk:
        raise _error('project', UNKNOWN_MESSAGE)
    if project.is_archived and not allow_archived:
        raise _error('project', ARCHIVED_MESSAGE)
    return project


# -- Reading -------------------------------------------------------------------------------------


def list_projects(user: User, *, archived: bool | None = False) -> QuerySet:
    """The user's projects in their order, each with `open_count` and `closed_count` (its tasks
    outside the Trash, subtasks included), in one query. `archived=False` (the default) is the
    active ones, `True` the archived ones, `None` both."""
    live = Q(tasks__deleted_at__isnull=True)
    queryset = Project.objects.filter(user=user)
    if archived is not None:
        queryset = queryset.filter(archived_at__isnull=not archived)
    # Ordered explicitly: `Meta.ordering` is dropped from a query that aggregates.
    return queryset.annotate(
        open_count=Count('tasks', filter=live & Q(tasks__completed_at__isnull=True)),
        closed_count=Count('tasks', filter=live & Q(tasks__completed_at__isnull=False)),
    ).order_by('position', 'name')


def with_counts(project: Project) -> Project:
    """`project` loaded with its counts (`list_projects`)."""
    return list_projects(project.user, archived=None).get(pk=project.pk)


def unfiled_count(user: User) -> int:
    """How many of the user's open, non-trashed tasks are in no project."""
    return Task.objects.filter(
        user=user, project__isnull=True, completed_at__isnull=True, deleted_at__isnull=True
    ).count()


def overdue_count(project: Project, today) -> int:
    """How many of the project's open tasks are overdue (none while it is archived; a task that
    is On hold or Blocked is never overdue)."""
    if project.is_archived:
        return 0
    return (
        Task.objects.filter(
            project=project,
            completed_at__isnull=True,
            deleted_at__isnull=True,
            due_date__lt=today,
        )
        .exclude(state__in=MUTED_STATES)
        .count()
    )


def outside_blocked_count(project: Project) -> int:
    """How many open tasks *outside* the project are blocked by an open task inside it: they stay
    Blocked while the project is archived, which the Archive question says."""
    pairs = TaskBlocker.objects.filter(
        blocker__project=project,
        blocker__completed_at__isnull=True,
        blocker__deleted_at__isnull=True,
        blocked__completed_at__isnull=True,
        blocked__deleted_at__isnull=True,
    ).values_list('blocked_id', 'blocked__project_id')
    return len({blocked for blocked, its_project in pairs if its_project != project.pk})


# -- Create, change, order -----------------------------------------------------------------------


def create_project(user: User, name: str, color: str = '', description: str = '') -> Project:
    """Create a project at the end of the user's list. Names are unique per user, ignoring case
    (archived ones included); at most `TodoSettings.max_projects_per_user`, archived ones counted."""
    cleaned_name = clean_name(name)
    cleaned_color = clean_color(color)
    cleaned_description = clean_description(description)
    cap = TodoSettings.load().max_projects_per_user
    get_account(user)
    with transaction.atomic():
        lock_account(user.pk)  # so racing creations can't pass the cap together
        existing = Project.objects.filter(user=user)
        if existing.count() >= cap:
            raise _error(
                NON_FIELD_ERRORS, f'You can have up to {cap} project{"s" if cap != 1 else ""}.'
            )
        _check_unique(user, cleaned_name)
        position = (existing.aggregate(top=Max('position'))['top'] or 0) + 1
        try:
            with transaction.atomic():
                return Project.objects.create(
                    user=user,
                    name=cleaned_name,
                    color=cleaned_color,
                    description=cleaned_description,
                    position=position,
                )
        except IntegrityError:  # a concurrent create of the same name
            raise _error('name', DUPLICATE_MESSAGE) from None


def update_project(
    project: Project,
    *,
    name: str | None = None,
    color: str | None = None,
    description: str | None = None,
) -> Project:
    """Change what is given (`None` leaves a field as it is; an empty description clears it). An
    archived project can still be renamed, recoloured and described."""
    fields = []
    if name is not None:
        project.name = clean_name(name)
        _check_unique(project.user, project.name, exclude=project)
        fields.append('name')
    if color is not None:
        project.color = clean_color(color)
        fields.append('color')
    if description is not None:
        project.description = clean_description(description)
        fields.append('description')
    if fields:
        try:
            with transaction.atomic():
                project.save(update_fields=fields)
        except IntegrityError:
            raise _error('name', DUPLICATE_MESSAGE) from None
    return project


def reorder_projects(user: User, ids: Sequence[UUID]) -> int:
    """Write `position = index` for the given ids (the user's own; others are ignored)."""
    own = {project.pk: project for project in Project.objects.filter(user=user, pk__in=ids)}
    changed = []
    for index, pk in enumerate(ids):
        project = own.get(pk)
        if project is not None:
            project.position = index
            changed.append(project)
    with transaction.atomic():
        Project.objects.bulk_update(changed, ['position'])
    return len(changed)


# -- Archive -------------------------------------------------------------------------------------


def _held_tasks(queryset: QuerySet, now) -> list[Task]:
    """Of `queryset`, the open, non-trashed tasks that are not muted by their own state and have a
    reminder that is unsent and already due: the ones owed a reminder when their project stops
    being archived. Their owner is loaded (`reminders.release_held` needs it)."""
    return list(
        queryset.filter(
            completed_at__isnull=True,
            deleted_at__isnull=True,
            reminders__sent_at__isnull=True,
            reminders__fire_at__lte=now,
        )
        .exclude(state__in=MUTED_STATES)
        .select_related('user')
        .distinct()
    )


def _release(tasks: Sequence[Task], now) -> int:
    """`reminders.release_held` for each task (it never raises: a delivery that fails leaves the
    reminders for the job)."""
    return sum(1 for task in tasks if reminders.release_held(task, now))


def archive_project(project: Project) -> bool:
    """Archive the project: nothing is deleted, its open tasks leave the lists and their reminders
    wait (see the module docstring). Returns whether this call archived it (False: it already
    was)."""
    now = timezone.now()
    with transaction.atomic():
        lock_account(project.user_id)
        won = Project.objects.filter(pk=project.pk, archived_at__isnull=True).update(
            archived_at=now
        )
    if won:
        project.archived_at = now
    else:
        project.refresh_from_db()
    return bool(won)


def unarchive_project(project: Project) -> bool:
    """Take the project out of the archive: its open tasks are back on the lists, and each one
    that held reminders gets one for what it missed. Returns whether this call unarchived it."""
    now = timezone.now()
    with transaction.atomic():
        lock_account(project.user_id)
        won = Project.objects.filter(pk=project.pk, archived_at__isnull=False).update(
            archived_at=None
        )
        if won:
            _release(_held_tasks(Task.objects.filter(project=project), now), now)
    project.refresh_from_db()
    return bool(won)


def delete_project(project: Project) -> int:
    """Delete the project and keep its tasks, which are then in no project (each gets an automatic
    log entry). The tasks of an archived project come back to the lists, with a reminder for the
    ones that held any. Returns how many tasks were in it."""
    now = timezone.now()
    with transaction.atomic():
        lock_account(project.user_id)
        current = Project.objects.filter(pk=project.pk).first()
        if current is None:
            return 0
        rows = list(Task.objects.filter(project=current).values_list('pk', 'user_id'))
        ids = [pk for pk, _ in rows]
        Task.objects.filter(pk__in=ids).update(project=None, updated_at=now)
        data = {'from': current.name, 'to': None, 'reason': 'deleted'}
        events.record_many(
            events.Entry(pk, user_id, EventKind.PROJECT, dict(data), True) for pk, user_id in rows
        )
        was_archived = current.is_archived
        current.delete()
        if was_archived:
            _release(_held_tasks(Task.objects.filter(pk__in=ids), now), now)
    return len(rows)


# -- Moving a task -------------------------------------------------------------------------------


def followed_parent(task: Task, old: Project | None, new: Project | None, parent: Task) -> None:
    """`task` was just moved under `parent` and took its project (`update_task`, in its
    transaction): record the automatic `project` entry, and release the reminders the task held
    if the project it left was archived and the one it is in now is not."""
    events.record(
        task,
        EventKind.PROJECT,
        {
            'from': old.name if old is not None else None,
            'to': new.name if new is not None else None,
            'via': events.task_ref(parent),
        },
        automatic=True,
    )
    if old is not None and old.is_archived and (new is None or not new.is_archived):
        now = timezone.now()
        _release(_held_tasks(Task.objects.filter(pk=task.pk), now), now)


def check_move(task: Task, project: Project | str | UUID | None) -> Project | None:
    """What `move_task` would refuse that can be told without writing: a task in the Trash, a
    subtask, a project that isn't the user's or is archived. Returns the cleaned project. For a
    caller that wants to refuse before spending a rate-limit budget."""
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
    if task.parent_id is not None:
        raise _error('project', SUBTASK_MESSAGE)
    return clean_project(task.user, project)


def move_task(task: Task, project: Project | str | UUID | None) -> Task:
    """Put the task in `project` (`None`: in no project). Its subtasks move with it, the closed
    and the trashed ones too. Open or closed, a task can move; one in the Trash, or a subtask on
    its own, cannot. Moving it out of an archived project releases the reminders it held.

    The task's activity log gets a `project` entry (old and new name) and each subtask an
    automatic one naming the parent. The project the task already has is a no-op."""
    target = check_move(task, project)
    now = timezone.now()
    with transaction.atomic():
        lock_account(task.user_id)
        subtask_ids = list(Task.objects.filter(parent=task).values_list('pk', flat=True))
        locked = lock_tasks(task.pk, *subtask_ids)
        current = locked.get(task.pk)
        if current is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        if current.is_trashed:
            raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
        if current.parent_id is not None:
            raise _error('project', SUBTASK_MESSAGE)
        if target is not None:
            # Re-read: archived (or deleted) since the check.
            target = clean_project(
                task.user, Project.objects.filter(pk=target.pk).first() or target.pk
            )
        new_id = target.pk if target is not None else None
        if current.project_id == new_id:
            task.project = target
            return task
        old = Project.objects.filter(pk=current.project_id).first() if current.project_id else None
        Task.objects.filter(pk__in=list(locked)).update(project=target, updated_at=now)
        data: dict[str, Any] = {
            'from': old.name if old is not None else None,
            'to': target.name if target is not None else None,
        }
        via = events.task_ref(current)
        events.record_many(
            [events.entry(current, EventKind.PROJECT, data)]
            + [
                events.entry(sub, EventKind.PROJECT, data | {'via': via}, automatic=True)
                for pk, sub in locked.items()
                if pk != task.pk
            ]
        )
        if old is not None and old.is_archived:
            _release(_held_tasks(Task.objects.filter(pk__in=list(locked)), now), now)
    task.project = target
    task.updated_at = now
    return task
