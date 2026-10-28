"""Schemas for the task endpoints (`/api/todo/tasks`)."""

import datetime as dt
from datetime import date, datetime, time
from typing import Annotated
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from ..limits import MAX_REORDER_IDS, TITLE_MAX
from .attachment import AttachmentSchema
from .label import LabelRefSchema
from .reminder import ReminderCreateSchema, ReminderSchema

#: A title is refused over `TITLE_MAX` before anything else is done with it (the AI reading in
#: particular); a 422.
Title = Annotated[str, Field(max_length=TITLE_MAX)]


class RecurrenceSchema(Schema):
    """The repeat rule of a task: `rrule` (an RFC 5545 `RRULE` value without the prefix),
    `rrule_text` the same in words, `mode` `fixed` or `from_completion`, `anchor_date` the date
    the current occurrence was scheduled for by the rule (postponing never moves it) and
    `ended_at` when the repeat was stopped or ran out (`null` while it is running)."""

    rrule: str
    rrule_text: str
    mode: str
    anchor_date: date
    ended_at: datetime | None


class TaskSchema(Schema):
    """One task, as every task endpoint returns it.

    `number` is the user's own (from 1000) and what the other endpoints take; `id` is the row's
    UUID. `description_html` is the Markdown description rendered and sanitised, with `#1234` and
    task URLs linked. The due date and time are the owner's local date and wall-clock time; `due_at`
    is the UTC instant they mean (a date-only task is due at the account's default due time).
    `parent` is the parent's number (`null` for a top-level task). `charged_credits` is what the
    task cost to create and `unpaid` says that price is still owed (see the 409 of `POST
    /tasks`). `subtask_count` and `open_subtask_count` count the subtasks that aren't in the
    Trash, `comment_count` the comments that aren't deleted and `attachment_count` the uploaded
    files.
    """

    id: UUID
    number: int
    title: str
    description: str
    description_html: str
    priority: int
    due_date: date | None
    due_time: time | None
    due_at: datetime | None
    completed_at: datetime | None
    deleted_at: datetime | None
    position: int
    source: str
    parent: int | None
    labels: list[LabelRefSchema]
    recurrence: RecurrenceSchema | None
    reminders: list[ReminderSchema]
    subtask_count: int
    open_subtask_count: int
    comment_count: int
    attachment_count: int
    charged_credits: int
    unpaid: bool
    created_at: datetime
    updated_at: datetime


class ReferencedBySchema(Schema):
    """A task that mentions this one, in its description (`comment` is `null`) or in a comment."""

    number: int
    title: str
    comment: UUID | None


class TaskDetailSchema(TaskSchema):
    """`GET /tasks/{number}`: the task plus its uploaded `attachments` and the tasks that mention
    it (`referenced_by`; nothing for a task in the Trash)."""

    attachments: list[AttachmentSchema]
    referenced_by: list[ReferencedBySchema]


class ParsedInfoSchema(Schema):
    """What `parse_title` did when a task was created: `applied` says whether the date, time and
    repeat read from the title were used (and the title cleaned of those words); otherwise `reason`
    says why not. `label` is the interpretation in words (`Sun 1 Nov, 09:00 · Every month ...`)."""

    applied: bool
    label: str
    matched_text: str
    reason: str


class TaskCreatedSchema(TaskSchema):
    """`POST /tasks`: the new task, and `parsed` (only when `parse_title` was asked for)."""

    parsed: ParsedInfoSchema | None


class TaskListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[TaskSchema]


class UnpaidBlockSchema(Schema):
    """The 409 of `POST /tasks`: creation is blocked until the listed repeating-task occurrences
    (which could not be paid for when their predecessor was completed) are settled by a top-up."""

    detail: str
    unpaid_task_numbers: list[int]


class TaskCreateSchema(Schema):
    """Body for `POST /tasks`. `labels` are label ids, `parent` a task number (a subtask; one level
    deep), `rrule` an RFC 5545 rule with `recurrence_mode` `fixed` (default) or `from_completion`.

    `reminders` (`{minutes_before, channel}` items) replace the default reminder; leave it out for
    the account's default, send `[]` for none.

    `parse_title`: when true the title is read by the AI first and the date, time and repeat it
    finds are used for the ones this body doesn't set itself (and the title is cleaned of those
    words); an AI that is not configured, busy, over its limits or that finds nothing just creates
    the task as typed. `parsed` in the answer says which.

    A key that is not one of these is refused with a 422.
    """

    model_config = ConfigDict(extra='forbid')

    title: Title
    description: str = ''
    priority: int = 0
    due_date: date | None = None
    due_time: time | None = None
    labels: list[UUID] = []
    parent: int | None = None
    rrule: str = ''
    recurrence_mode: str = 'fixed'
    reminders: list[ReminderCreateSchema] | None = None
    parse_title: bool = False


class TaskUpdateSchema(Schema):
    """Body for `PATCH /tasks/{number}`. Every field is optional and only the ones present are
    applied; an explicit `null` **clears** the field (`description` to empty, `priority` to none,
    `due_date` and `due_time`, `labels` to no labels, `parent` to a top-level task, `rrule` to
    stop repeating). A `title` cannot be empty, so `null` there is a 400.

    Clearing `due_date` clears the time with it. A key that is not one of these (a typo, `status`,
    `reminders`, ...) is refused with a 422 rather than ignored.
    """

    model_config = ConfigDict(extra='forbid')

    title: Title | None = None
    description: str | None = None
    priority: int | None = None
    due_date: date | None = None
    due_time: time | None = None
    labels: list[UUID] | None = None
    parent: int | None = None
    rrule: str | None = None
    recurrence_mode: str | None = None


class PostponeSchema(Schema):
    """Body for `POST /tasks/{number}/postpone`: `preset` `tomorrow`, `next_week`, `next_weekend`
    or `date` (with `date`, today or later)."""

    model_config = ConfigDict(extra='forbid')

    preset: str
    # `dt.date`: a bare `date` would name this very field inside the class body.
    date: dt.date | None = None


class CountSchema(Schema):
    count: int


class ReorderSchema(Schema):
    """Body for `POST /tasks/reorder`: `scope` `today` (the tasks due today) or `subtasks` (the
    subtasks of `parent`, a task number), and the task ids in the new order (at most 500; a longer
    list is a 422). Ids outside the set are ignored."""

    model_config = ConfigDict(extra='forbid')

    scope: str
    ids: Annotated[list[UUID], Field(max_length=MAX_REORDER_IDS)]
    parent: int | None = None


class ChecklistSchema(Schema):
    """Body for `POST /tasks/{number}/checklist`: tick or untick the `index`th checklist item
    (counted from 0) of the description."""

    model_config = ConfigDict(extra='forbid')

    index: int
    checked: bool
