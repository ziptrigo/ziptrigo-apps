"""Schemas for the project endpoints (`/api/todo/projects`, issue #314)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from ..limits import MAX_REORDER_IDS, MAX_SECTIONS_PER_PROJECT, PROJECT_PAGE_LIMIT


class ProjectRefSchema(Schema):
    """The project a task is in, as the task carries it. `archived`: the project is archived, so
    the task is off the `today`, `upcoming` and `all` lists, never overdue, and its reminders wait."""

    id: UUID
    name: str
    color: str
    archived: bool


class SectionRefSchema(Schema):
    """The section of its project a task is in."""

    id: UUID
    name: str


class SectionSchema(Schema):
    """A section of a project: a named group of its tasks, in the project's order of sections."""

    id: UUID
    name: str
    position: int


class ProjectSchema(Schema):
    """One of the caller's projects. A task is in at most one project (a subtask in its parent's).

    `description` is Markdown and `description_html` the same rendered and sanitised. `archived_at`
    is set while the project is archived: its open tasks are then left out of the `today`,
    `upcoming` and `all` lists, are never overdue, and their reminders wait until it is unarchived.
    `open_count` and `closed_count` count its tasks outside the Trash, subtasks included.
    `sections` are in their order."""

    id: UUID
    name: str
    color: str
    description: str
    description_html: str
    position: int
    archived_at: datetime | None
    open_count: int
    closed_count: int
    sections: list[SectionSchema]
    created_at: datetime


class ProjectCreateSchema(Schema):
    """Body for `POST /projects`. `color` is `#rrggbb` (empty: the default colour); `description`
    is Markdown, up to 10,000 characters. Names are unique among the caller's projects, ignoring
    case, archived ones included."""

    model_config = ConfigDict(extra='forbid')

    name: str
    color: str = ''
    description: str = ''


class ProjectUpdateSchema(Schema):
    """Body for `PATCH /projects/{id}`: only the fields present are applied. An explicit `null`
    clears `description` (to empty) and `color` (to the default); a `name` cannot be empty, so
    `null` there is a 400. A key that is not one of these is a 422."""

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    color: str | None = None
    description: str | None = None


class ProjectOrderSchema(Schema):
    """Body for `POST /projects/reorder`: the project ids in the new order (ids that aren't yours
    are ignored; at most 500)."""

    ids: Annotated[list[UUID], Field(max_length=MAX_REORDER_IDS)]


class SectionNameSchema(Schema):
    """Body for adding or renaming a section: its name (unique in the project, ignoring case; up
    to 80 characters)."""

    model_config = ConfigDict(extra='forbid')

    name: str


class SectionOrderSchema(Schema):
    """Body for `POST /projects/{id}/sections/reorder`: the section ids in the new order (ids that
    aren't this project's are ignored)."""

    ids: Annotated[list[UUID], Field(max_length=MAX_SECTIONS_PER_PROJECT)]


class ProjectTaskOrderSchema(Schema):
    """Body for `POST /projects/{id}/tasks/reorder`: the hand-made order of **one list** of the
    project's page. `section` is the list: a section id, or `null` for the tasks with no section.
    `ids` are task ids (not numbers) in the order wanted; ids that are not the caller's non-trashed
    tasks of this project are ignored. A task listed that is in another section **joins this one**,
    with its subtasks; a subtask listed alone stays with its parent."""

    model_config = ConfigDict(extra='forbid')

    section: UUID | None = None
    ids: Annotated[list[UUID], Field(max_length=PROJECT_PAGE_LIMIT)]
