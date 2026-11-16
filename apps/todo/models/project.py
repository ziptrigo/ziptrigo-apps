import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower

from apps.accounts.models import User

from ..limits import PROJECT_NAME_MAX, SECTION_NAME_MAX
from .label import DEFAULT_LABEL_COLOR


class Project(models.Model):
    """One of a user's projects (issue #314): a flat list, a task is in at most one (or in none),
    and a subtask is always in its parent's. A project has a colour, a Markdown description and a
    place in the user's order.

    **Archived** (`archived_at`) hides the project without deleting anything: its open tasks leave
    Today, Upcoming and All, are never overdue and their reminders wait, like a task that is On hold
    (`services.projects`). Deleting a project keeps its tasks, which are then in no project.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `Task.project`.
    tasks: ClassVar['models.Manager']
    pk: uuid.UUID
    user_id: uuid.UUID
    # Set by `services.projects.list_projects` (not stored): tasks outside the Trash, subtasks included.
    open_count: int
    closed_count: int

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='todo_projects'
        ),
    )
    name = cast(str, models.CharField(max_length=PROJECT_NAME_MAX))
    color = cast(
        str,
        models.CharField(
            max_length=7,
            default=DEFAULT_LABEL_COLOR,
            validators=[RegexValidator(r'^#[0-9a-f]{6}$', 'Use a colour like #8fa89e.')],
        ),
    )
    #: Markdown, rendered like a task's description (without task links' checklists being editable).
    description = cast(str, models.TextField(blank=True, default=''))
    position = cast(int, models.PositiveIntegerField(default=0))
    archived_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['position', 'name']
        constraints = [
            models.UniqueConstraint(Lower('name'), 'user', name='todo_project_name_user'),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def task_total(self) -> int:
        """Open plus closed tasks outside the Trash (needs `list_projects`' counts)."""
        return self.open_count + self.closed_count

    @property
    def progress_percent(self) -> int:
        """How much of the project is closed, 0 to 100 (0 for an empty one)."""
        total = self.task_total
        return round(100 * self.closed_count / total) if total else 0


class Section(models.Model):
    """A named group inside a project (issue #314, part 2): Backlog, This week, Later. A task of the
    project is in at most one of its sections, or in none; a subtask is always in its parent's.
    Deleting a section keeps its tasks, which stay in the project with no section."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    #: The reverse accessor of `Task.section`.
    tasks: ClassVar['models.Manager']
    pk: uuid.UUID
    project_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = cast(
        'Project',
        models.ForeignKey(Project, on_delete=models.CASCADE, related_name='sections'),
    )
    name = cast(str, models.CharField(max_length=SECTION_NAME_MAX))
    position = cast(int, models.PositiveIntegerField(default=0))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['position', 'created_at']
        constraints = [
            models.UniqueConstraint(Lower('name'), 'project', name='todo_section_name_project'),
        ]

    def __str__(self) -> str:
        return self.name
