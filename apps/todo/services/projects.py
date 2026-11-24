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

**Sections** (part 2) are named groups inside a project. A task is in at most one section of *its*
project, or in none; a subtask is in its parent's (`set_section` moves them together). A task that
moves to another project leaves its section. Deleting a section keeps its tasks, in the project with
no section. **The manual order** of a project's page is `Task.project_position`, an order among the
tasks of one section (or of the tasks without one): `reorder_tasks` writes it for the rows of one
list, and puts a row dragged in from another section into that section. A new or moved task goes to
the end of its group.

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

from ..models import (
    MUTED_STATES,
    EventKind,
    Project,
    Section,
    Task,
    TaskBlocker,
    TodoSettings,
)
from . import events, reminders
from .accounts import get_account, lock_account
from .labels import clean_color
from .limits import (
    MAX_SECTIONS_PER_PROJECT,
    PROJECT_DESCRIPTION_MAX,
    PROJECT_NAME_MAX,
    SECTION_NAME_MAX,
)
from .task_state import lock_tasks

logger = logging.getLogger(__name__)

DUPLICATE_MESSAGE = 'You already have a project with this name.'
ARCHIVED_MESSAGE = 'This project is archived. Unarchive it to add tasks to it.'
UNKNOWN_MESSAGE = 'Unknown project.'
UNKNOWN_SECTION_MESSAGE = 'Unknown section.'
SECTION_DUPLICATE_MESSAGE = 'This project already has a section with this name.'
ARCHIVED_SECTIONS_MESSAGE = 'Unarchive the project to change its sections.'
SUBTASK_MESSAGE = "A subtask is in its parent's project. Move the parent instead."
SUBTASK_SECTION_MESSAGE = "A subtask is in its parent's section. Move the parent instead."
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


def check_new_project(user: User, name: str, color: str = '', description: str = '') -> None:
    """What `create_project` would refuse that can be told without writing or locking: the
    fields, a name already used and the cap. For a caller that wants to refuse before spending a
    rate-limit budget; `create_project` decides again under the lock."""
    cleaned = clean_name(name)
    clean_color(color)
    clean_description(description)
    cap = TodoSettings.load().max_projects_per_user
    if Project.objects.filter(user=user).count() >= cap:
        raise _error(NON_FIELD_ERRORS, _cap_message(cap))
    _check_unique(user, cleaned)


def check_project_name(project: Project, name: str) -> None:
    """A new name for `project`, checked without writing (its shape, and that no other of the
    user's projects has it)."""
    _check_unique(project.user, clean_name(name), exclude=project)


def _cap_message(cap: int) -> str:
    return f'You can have up to {cap} project{"s" if cap != 1 else ""}.'


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
            raise _error(NON_FIELD_ERRORS, _cap_message(cap))
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
    """Put the user's projects with these ids in the order given (others are ignored).

    The listed projects swap the places they already hold, so a partial list (the sidebar shows
    only the first 20 projects) never moves one onto a place a project outside the request holds.
    If the listed projects share a position (older data), every project of the user is numbered
    first, keeping the order they are shown in."""
    wanted = list(dict.fromkeys(ids))
    with transaction.atomic():

        def load() -> list[Project]:
            own = {p.pk: p for p in Project.objects.filter(user=user, pk__in=wanted)}
            return [own[pk] for pk in wanted if pk in own]

        ordered = load()
        places = sorted(project.position for project in ordered)
        if len(set(places)) != len(places):
            everyone = list(Project.objects.filter(user=user).order_by('position', 'name'))
            for index, project in enumerate(everyone):
                project.position = index
            Project.objects.bulk_update(everyone, ['position'])
            ordered = load()
            places = sorted(project.position for project in ordered)
        changed = []
        for project, place in zip(ordered, places, strict=True):
            if project.position != place:
                project.position = place
                changed.append(project)
        Project.objects.bulk_update(changed, ['position'])
    return len(ordered)


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
        Task.objects.filter(pk__in=ids).update(
            project=None, section=None, project_position=0, updated_at=now
        )
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


def followed_parent(
    task: Task,
    old: Project | None,
    new: Project | None,
    parent: Task,
    *,
    project_changed: bool = True,
    old_section: Section | None = None,
    new_section: Section | None = None,
) -> None:
    """`task` was just moved under `parent` and took its project and section (`update_task`, in
    its transaction): record the automatic `project` entry (when the project changed) and
    `section` entry (when the section did), and release the reminders the task held if the project
    it left was archived and the one it is in now is not."""
    via = events.task_ref(parent)
    entries = []
    if project_changed:
        entries.append(
            events.entry(
                task,
                EventKind.PROJECT,
                {
                    'from': old.name if old is not None else None,
                    'to': new.name if new is not None else None,
                    'via': via,
                },
                automatic=True,
            )
        )
    if (old_section.pk if old_section else None) != (new_section.pk if new_section else None):
        entries.append(
            events.entry(
                task,
                EventKind.SECTION,
                {
                    'from': old_section.name if old_section is not None else None,
                    'to': new_section.name if new_section is not None else None,
                    'via': via,
                },
                automatic=True,
            )
        )
    events.record_many(entries)
    if not project_changed:
        return
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
    """Put the task in `project` (`None`: in no project), in no section, at the end of the project's
    manual order. Its subtasks move with it, the closed
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
        Task.objects.filter(pk__in=list(locked)).update(
            project=target,
            section=None,  # a section belongs to the project left behind
            project_position=next_position(target, None) if target is not None else 0,
            updated_at=now,
        )
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
    task.section = None
    task.updated_at = now
    return task


# -- Sections ------------------------------------------------------------------------------------


def clean_section_name(name: object) -> str:
    cleaned = ' '.join(str(name or '').split())
    if not cleaned:
        raise _error('name', 'Name is required.')
    if len(cleaned) > SECTION_NAME_MAX:
        raise _error('name', f'Name is too long (at most {SECTION_NAME_MAX} characters).')
    return cleaned


def _check_section_unique(project: Project, name: str, *, exclude: Section | None = None) -> None:
    taken = Section.objects.filter(project=project, name__iexact=name)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise _error('name', SECTION_DUPLICATE_MESSAGE)


def require_active(project: Project) -> None:
    if project.is_archived:
        raise _error(NON_FIELD_ERRORS, ARCHIVED_SECTIONS_MESSAGE)


def list_sections(project: Project) -> list[Section]:
    """The project's sections in their order."""
    return list(Section.objects.filter(project=project).order_by('position', 'created_at'))


def clean_section(project: Project | None, section: Section | str | UUID | None) -> Section | None:
    """`section` (a section, its id, or nothing) as a section of `project`, or `None` for none. One
    of another project, an unknown or malformed id, and any section when there is no project, are
    all "Unknown section."."""
    if section is None or section == '':
        return None
    if project is None:
        raise _error('section', UNKNOWN_SECTION_MESSAGE)
    if not isinstance(section, Section):
        try:
            found = Section.objects.filter(project=project, pk=section).first()
        except ValueError, ValidationError:  # a malformed id
            found = None
        if found is None:
            raise _error('section', UNKNOWN_SECTION_MESSAGE)
        return found
    if section.project_id != project.pk:
        raise _error('section', UNKNOWN_SECTION_MESSAGE)
    return section


def next_position(project: Project, section: Section | None) -> int:
    """`Task.project_position` of a task that joins `section` of `project` (`None`: its tasks with
    no section): after the others."""
    top = Task.objects.filter(project=project, section=section, deleted_at__isnull=True).aggregate(
        top=Max('project_position')
    )['top']
    return (top or 0) + 1


def check_new_section(project: Project, name: str) -> None:
    """What `create_section` would refuse without writing: the name, an archived project, the
    cap and a name the project already has."""
    cleaned = clean_section_name(name)
    require_active(project)
    if Section.objects.filter(project=project).count() >= MAX_SECTIONS_PER_PROJECT:
        raise _error(NON_FIELD_ERRORS, _section_cap_message())
    _check_section_unique(project, cleaned)


def _section_cap_message() -> str:
    return f'A project can have up to {MAX_SECTIONS_PER_PROJECT} sections.'


def check_section_name(section: Section, name: str) -> None:
    """A new name for `section`, checked without writing."""
    cleaned = clean_section_name(name)
    require_active(section.project)
    _check_section_unique(section.project, cleaned, exclude=section)


def create_section(project: Project, name: str) -> Section:
    """Add a section at the end of the project's. Names are unique in a project, ignoring case; at
    most `MAX_SECTIONS_PER_PROJECT`. An archived project's sections can't be changed."""
    cleaned = clean_section_name(name)
    require_active(project)
    with transaction.atomic():
        lock_account(project.user_id)
        existing = Section.objects.filter(project=project)
        if existing.count() >= MAX_SECTIONS_PER_PROJECT:
            raise _error(NON_FIELD_ERRORS, _section_cap_message())
        _check_section_unique(project, cleaned)
        position = (existing.aggregate(top=Max('position'))['top'] or 0) + 1
        try:
            with transaction.atomic():
                return Section.objects.create(project=project, name=cleaned, position=position)
        except IntegrityError:  # a concurrent create of the same name
            raise _error('name', SECTION_DUPLICATE_MESSAGE) from None


def rename_section(section: Section, name: str) -> Section:
    cleaned = clean_section_name(name)
    require_active(section.project)
    _check_section_unique(section.project, cleaned, exclude=section)
    try:
        with transaction.atomic():
            lock_account(section.project.user_id)
            _check_section_unique(section.project, cleaned, exclude=section)
            section.name = cleaned
            section.save(update_fields=['name'])
    except IntegrityError:
        raise _error('name', SECTION_DUPLICATE_MESSAGE) from None
    return section


def delete_section(section: Section) -> int:
    """Delete the section and keep its tasks, which stay in the project with no section, at the end
    of that group (each gets an automatic log entry). Returns how many tasks were in it."""
    require_active(section.project)
    now = timezone.now()
    with transaction.atomic():
        lock_account(section.project.user_id)
        current = Section.objects.select_related('project').filter(pk=section.pk).first()
        if current is None:
            return 0
        rows = list(
            Task.objects.filter(section=current)
            .order_by('project_position', 'created_at')
            .values_list('pk', 'user_id')
        )
        start = next_position(current.project, None)
        Task.objects.bulk_update(
            [
                Task(pk=pk, section=None, project_position=start + index, updated_at=now)
                for index, (pk, _) in enumerate(rows)
            ],
            ['section', 'project_position', 'updated_at'],
            batch_size=500,
        )
        data = {'from': current.name, 'to': None, 'reason': 'deleted'}
        events.record_many(
            events.Entry(pk, user_id, EventKind.SECTION, dict(data), True) for pk, user_id in rows
        )
        current.delete()
    return len(rows)


def reorder_sections(project: Project, ids: Sequence[UUID]) -> int:
    """Write `position = index` for the given ids (the project's own sections; others are ignored).
    Refused for an archived project, like every other change to its sections."""
    require_active(project)
    own = {section.pk: section for section in Section.objects.filter(project=project, pk__in=ids)}
    changed = []
    for index, pk in enumerate(ids):
        section = own.get(pk)
        if section is not None:
            section.position = index
            changed.append(section)
    with transaction.atomic():
        lock_account(project.user_id)
        Section.objects.bulk_update(changed, ['position'])
    return len(changed)


def _family(task_ids: Sequence[UUID]) -> list[UUID]:
    """The ids of the subtasks of `task_ids` (they follow their parent's section)."""
    return list(Task.objects.filter(parent_id__in=list(task_ids)).values_list('pk', flat=True))


def check_section(task: Task, section: Section | str | UUID | None) -> Section | None:
    """What `set_section` would refuse that can be told without writing. Returns the cleaned
    section."""
    if task.is_trashed:
        raise _error(NON_FIELD_ERRORS, 'Restore the task before changing it.')
    if task.parent_id is not None:
        raise _error('section', SUBTASK_SECTION_MESSAGE)
    project = task.project if task.project_id is not None else None
    cleaned = clean_section(project, section)
    if project is not None and (cleaned.pk if cleaned else None) != task.section_id:
        require_active(project)  # an archived project's sections are frozen, tasks' places too
    return cleaned


def set_section(task: Task, section: Section | str | UUID | None) -> Task:
    """Put the task in `section` of its project (`None`: in no section), at the end of that group.
    Its subtasks go with it. Logged on the task (and, automatically, on each subtask). The section
    it already has is a no-op."""
    target = check_section(task, section)
    now = timezone.now()
    with transaction.atomic():
        lock_account(task.user_id)
        subtask_ids = _family([task.pk])
        locked = lock_tasks(task.pk, *subtask_ids)
        current = locked.get(task.pk)
        if current is None:
            raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
        target = check_section(current, target.pk if target is not None else None)
        new_id = target.pk if target is not None else None
        if current.section_id == new_id:
            task.section = target
            return task
        old = Section.objects.filter(pk=current.section_id).first() if current.section_id else None
        project = current.project
        assert project is not None or target is None
        Task.objects.filter(pk__in=list(locked)).update(
            section=target,
            project_position=next_position(project, target) if project is not None else 0,
            updated_at=now,
        )
        _record_section(locked, task.pk, old, target, events.task_ref(current))
    task.section = target
    task.updated_at = now
    return task


def _record_section(
    locked: dict[UUID, Task],
    parent_pk: UUID,
    old: Section | None,
    new: Section | None,
    via: dict[str, Any],
) -> None:
    data: dict[str, Any] = {
        'from': old.name if old is not None else None,
        'to': new.name if new is not None else None,
    }
    events.record_many(
        events.entry(
            item,
            EventKind.SECTION,
            data | ({} if pk == parent_pk else {'via': via}),
            automatic=pk != parent_pk,
        )
        for pk, item in locked.items()
    )


def reorder_tasks(
    user: User, project: Project, section: Section | str | UUID | None, ids: Sequence[UUID]
) -> int:
    """Write the manual order of one list of the project's page: `section` of `project` (`None`:
    its tasks with no section). `ids` are the tasks in the order wanted; ids that are not the
    user's non-trashed tasks of the project are ignored.

    **The tasks listed swap the places they already hold in the list** (the ones sent refill those
    places in the order sent), and the whole list is then numbered by place, so a caller that sends
    only some of the list's tasks (an API client, a page that lists the open ones while closed
    ones keep their places) never makes two tasks tie. A task listed that is in another section (a
    row dragged across) **joins this one**, right after the listed task sent before it (first, when
    none was), with its subtasks, which take its place too; that move is logged. A subtask never
    changes section on its own: listed alone in another section it is skipped. Returns how many
    tasks were positioned. One bulk write for the rows whose place changed."""
    if project.user_id != user.pk:
        raise _error('project', UNKNOWN_MESSAGE)
    require_active(project)
    wanted = clean_section(project, section)
    now = timezone.now()
    with transaction.atomic():
        lock_account(user.pk)
        # Read again under the lock (by id): a section deleted meanwhile is "Unknown section.",
        # not a foreign-key error.
        section = clean_section(project, wanted.pk if wanted is not None else None)
        section_id = section.pk if section is not None else None
        sent = {
            task.pk: task
            for task in Task.objects.filter(
                user=user, project=project, deleted_at__isnull=True, pk__in=list(ids)
            ).select_related('user')
        }
        # The rows asked for, in order, without repeats, unknown ids, or a subtask that would
        # leave its parent's section.
        order = [
            pk
            for pk in dict.fromkeys(ids)
            if pk in sent
            and not (sent[pk].section_id != section_id and sent[pk].parent_id is not None)
        ]
        current = list(
            Task.objects.filter(project=project, section=section, deleted_at__isnull=True)
            .order_by('project_position', 'created_at', 'number')
            .values_list('pk', 'project_position')
        )
        here = {pk for pk, _ in current}
        staying = [pk for pk in order if pk in here]
        # The list as it will be: each place held by a listed task takes the next listed tasks up
        # to (and including) the next one that was already here; what is left goes at the end.
        queue = list(order)
        final: list[UUID] = []
        listed = set(staying)
        for pk, _ in current:
            if pk not in listed:
                final.append(pk)
                continue
            while queue:
                taken = queue.pop(0)
                final.append(taken)
                if taken in here:
                    break
        final += queue
        place = {pk: index for index, pk in enumerate(final)}

        joining = [pk for pk in order if pk not in here]
        for pk in joining:
            task = sent[pk]
            family = {sub.pk: sub for sub in Task.objects.filter(parent=task)}
            old = Section.objects.filter(pk=task.section_id).first() if task.section_id else None
            # Its subtasks come with it and take its place (the default order keeps them after it).
            Task.objects.filter(pk__in=[pk, *family]).update(
                section=section, project_position=place[pk], updated_at=now
            )
            _record_section({pk: task, **family}, pk, old, section, events.task_ref(task))
        held = dict(current)
        changed = [
            Task(pk=pk, project_position=index, updated_at=now)
            for pk, index in place.items()
            if pk in here and held[pk] != index
        ]
        Task.objects.bulk_update(changed, ['project_position', 'updated_at'], batch_size=500)
    return len(order)
