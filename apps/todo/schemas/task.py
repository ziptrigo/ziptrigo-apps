"""Schemas for the task endpoints (`/api/todo/tasks`)."""

import datetime as dt
from datetime import date, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from ..limits import MAX_BLOCKERS_PER_TASK, MAX_REORDER_IDS, TITLE_MAX
from .attachment import AttachmentSchema
from .label import LabelRefSchema
from .reminder import ReminderCreateSchema, ReminderSchema

#: The states and resolutions as responses name them (an `enum` in the OpenAPI document).
StateName = Literal['todo', 'in_progress', 'blocked', 'on_hold']
ResolutionName = Literal['done', 'wont_do']

#: The same values in a request body, which is read as a plain string on purpose: the services
#: refuse a bad one with a 400 and their own message (a state of `blocked`, which only blockers set,
#: in particular), where a closed enum would be ninja's list-valued 422. The `enum` documents the
#: choices for generated clients.
SettableState = Annotated[
    str, Field(json_schema_extra={'enum': ['todo', 'in_progress', 'on_hold']})
]
ResolutionChoice = Annotated[str, Field(json_schema_extra={'enum': ['done', 'wont_do']})]
SubtaskChoice = Annotated[str, Field(json_schema_extra={'enum': ['done', 'wont_do', 'leave']})]

#: A title is refused over `TITLE_MAX` before anything else is done with it (the AI reading in
#: particular); a 422.
Title = Annotated[str, Field(max_length=TITLE_MAX)]


class ErrorSchema(Schema):
    """An error answer that carries only a `detail` sentence (the 409 of `POST /close` on a task that
    is already closed, the 410 of the removed endpoints)."""

    detail: str


class RecurrenceSchema(Schema):
    """The repeat rule of a task: `rrule` (an RFC 5545 `RRULE` value without the prefix),
    `rrule_text` the same in words, `mode` `fixed` or `from_completion`, `anchor_date` the date
    the current occurrence was scheduled for by the rule (postponing never moves it) and
    `ended_at` when the repeat was stopped or ran out (`null` while it is running), and
    `open_occurrences` the number of open, non-trashed tasks that share this rule (the task itself
    included while it is open). With more than one, trashing an older occurrence with "also stop
    repeating" would end the rule for the newer open one too."""

    rrule: str
    rrule_text: str
    mode: str
    anchor_date: date
    ended_at: datetime | None
    open_occurrences: int


class TaskRefSchema(Schema):
    """A task another task is linked to (`blocked_by`, `blocks`). `open` says whether it is open:
    not closed and not in the Trash, which is when it still blocks what it blocks; `trashed` says it
    is in the Trash. A link is kept when either task closes or is trashed, so closed and trashed
    tasks are listed too (`open: false`)."""

    number: int = Field(description="The linked task's number.")
    title: str = Field(description="The linked task's title.")
    open: bool = Field(
        description='Not closed and not in the Trash: it still blocks what it blocks.'
    )
    trashed: bool = Field(description='In the Trash.')


class TaskSchema(Schema):
    """One task, as every task endpoint returns it.

    `number` is the user's own (from 1000) and what the other endpoints take; `id` is the row's
    UUID. `description_html` is the Markdown description rendered and sanitised, with `#1234` and
    task URLs linked. The due date and time are the owner's local date and wall-clock time; `due_at`
    is the UTC instant they mean (a date-only task is due at the account's default due time).
    `parent` is the parent's number (`null` for a top-level task). `charged_credits` is what the
    task cost to create and `unpaid` says that price is still owed (see the 409 of `POST
    /tasks`). `email_from` and `email_from_authenticated` say, for a task created from an email
    (`source` is `email`), who it came from (the From address as the sender wrote it) and whether
    the mail system authenticated that sender; both are `null` for every other task.
    `subtask_count` and `open_subtask_count` count the subtasks that aren't in the Trash,
    `comment_count` the comments that aren't deleted and `attachment_count` the uploaded files.

    **Open and closed.** A task is open while `closed_at` is `null` and closed from the moment it is
    closed (as Done or as Won't do), until it is reopened. `resolution` says how a closed task was
    closed (`done` or `wont_do`; a closed task with none recorded reads as `done`) and is `null` for
    an open one. `state` is what an *open* task is doing: `todo`, `in_progress`, `on_hold` (chosen
    by the owner) or `blocked` (set automatically while a task in `blocked_by` is open; nobody
    chooses it). `on_hold` and `blocked` are *muted*: such a task is never overdue and is not moved
    by `POST /tasks/reschedule-overdue`, and its reminders wait until it leaves the state. A closed
    task has no state: `state` is `null` while `closed_at` is set (reopening gives it `todo`, or
    `blocked` while a blocker is open). `has_open_blocker` is true when any task in `blocked_by` is
    open (`open: true`), whatever `state` says: an open task the owner moved to `in_progress` while
    a blocker was open still has one.

    `blocked_by` lists the tasks that block this one and `blocks` the tasks it blocks, by number
    (see `TaskRefSchema`); the links are kept when either task closes or is trashed.
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
    closed_at: datetime | None = Field(
        description="When the task was closed (as Done or as Won't do); `null` while it is open."
    )
    state: StateName | None = Field(
        description='What an open task is doing: `todo`, `in_progress`, `on_hold` or `blocked` '
        '(set only by blockers); `null` while the task is closed (`closed_at` is set).'
    )
    resolution: ResolutionName | None = Field(
        description='How a closed task was closed, `done` or `wont_do`; `null` while it is open.'
    )
    deleted_at: datetime | None
    position: int
    source: str
    email_from: str | None = Field(
        description='For a task made from an email: its From address; `null` otherwise.'
    )
    email_from_authenticated: bool | None = Field(
        description='For a task made from an email: whether the sender was authenticated '
        '(DMARC, DKIM or SPF); `null` otherwise.'
    )
    parent: int | None
    labels: list[LabelRefSchema]
    recurrence: RecurrenceSchema | None
    offers_stop_repeating: bool = Field(
        description='Whether trashing this task should offer "Also stop repeating": it is open, '
        'not in the Trash, its repeat is running and it is the only open occurrence of the rule '
        '(`recurrence.open_occurrences` is 1). `false` otherwise, for a closed, trashed or '
        'non-repeating task too. Trashing an older occurrence with stop repeating while a newer one '
        'is open would end the rule for that one as well, so a client offers the choice only '
        'when this is true. To act on it send `DELETE /tasks/{number}?stop_recurrence=true`, which '
        'trashes the task and stops the repeat in one request.'
    )
    reminders: list[ReminderSchema]
    blocked_by: list[TaskRefSchema] = Field(
        description='The tasks that block this one, by number (closed and trashed ones too).'
    )
    has_open_blocker: bool = Field(
        description='Whether any task in `blocked_by` is open (`open: true`).'
    )
    blocks: list[TaskRefSchema] = Field(
        description='The tasks this one blocks, by number (closed and trashed ones too).'
    )
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


class TaskListItemSchema(TaskSchema):
    """A task in a list (`GET /tasks`, `GET /filters/{id}/tasks`). When the list was searched
    (`q`, or a filter with search text) each task also says where the text matched:

    - `search_field` is `title`, `description` or `comment` (the first comment that matches), or
      `""` when no snippet could be made (the budget ran out before this row, or the engines
      disagree at the edges; the task still matches). `null` when there was no search text.
    - `search_snippet` is **plain text** around the match (about 60 characters either side, with
      an ellipsis `…` where the text goes on), `match_start` and `match_end` the half-open range of
      the matched text inside it, in Unicode code points (not UTF-16 units). Searches run on
      normalised text, so the snippet is lower-case with accents, punctuation and Markdown
      formatting gone, and a match longer than 100 characters is cut to its first 100. All three
      are `null` when `search_field` is `""` or null.
    """

    search_field: Literal['title', 'description', 'comment', ''] | None = Field(
        default=None,
        description='Where the search text matched; `null` when the list was not searched.',
    )
    search_snippet: str | None = Field(
        default=None, description='Plain text around the match; `null` without one.'
    )
    match_start: int | None = Field(
        default=None, description='Start of the match in `search_snippet` (code points).'
    )
    match_end: int | None = Field(
        default=None, description='End (exclusive) of the match in `search_snippet`.'
    )


class TaskListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[TaskListItemSchema]


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

    `state` is the state the task starts in: `todo` (the default), `in_progress` or `on_hold` (a 400
    for anything else: `blocked` is set only by blockers). `blocked_by` lists the numbers of the
    caller's own tasks that block the new one (at most 20, none in the Trash; one that doesn't exist,
    or isn't the caller's, is a 400 `There is no task #N.`); when one of them is open the task starts
    `blocked`, whatever `state` says.

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
    state: SettableState = Field(
        default='todo', description='The starting state: `todo`, `in_progress` or `on_hold`.'
    )
    blocked_by: Annotated[list[int], Field(max_length=MAX_BLOCKERS_PER_TASK)] = Field(
        default=[], description="Numbers of the caller's tasks that block the new one (at most 20)."
    )


class TaskUpdateSchema(Schema):
    """Body for `PATCH /tasks/{number}`. Every field is optional and only the ones present are
    applied; an explicit `null` **clears** the field (`description` to empty, `priority` to none,
    `due_date` and `due_time`, `labels` to no labels, `parent` to a top-level task, `rrule` to
    stop repeating). A `title` cannot be empty, so `null` there is a 400.

    `state` sets an open task's state: `todo`, `in_progress` or `on_hold` (a 400 for anything else,
    and for a closed task: reopen it first; it cannot be `null`). `blocked_by` **replaces** the set
    of tasks that block this one with the numbers given (the difference is linked and unlinked, all
    or nothing; `null` or `[]` removes every blocker): when one of them is open the task becomes
    `blocked`, and when none is left a `blocked` task goes back to `todo`. A closed task can be given
    blockers too (the links only).

    **`state` together with `blocked_by`:** the links are written first and then the task is
    `blocked` if any of its blockers is open (whatever `state` says), else it gets `state`. A
    `state` equal to the task's current state is a no-op, so a client may send back the state it
    read (`blocked` included); `blocked` as a change is a 400. A request that changes nothing (the
    same `blocked_by`, the same `state`, no other field) spends no budget.

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
    state: SettableState | None = Field(
        default=None, description="The open task's new state: `todo`, `in_progress` or `on_hold`."
    )
    blocked_by: Annotated[list[int], Field(max_length=MAX_BLOCKERS_PER_TASK)] | None = Field(
        default=None,
        description='Replaces the set of tasks that block this one (numbers, at most 20); '
        '`null` or `[]` removes them all.',
    )


class CloseSchema(Schema):
    """Body for `POST /tasks/{number}/close`. `resolution` is `done` (the default) or `wont_do`.

    `subtasks` says what happens to the open subtasks of the task being closed: `done` (the default)
    or `wont_do` closes them with that resolution, `leave` leaves them open.

    `note` (a Markdown text of up to 10,000 characters) is added to the task as a comment; it is
    allowed only with `resolution=wont_do` (a 400 otherwise), where it says why.

    A key that is not one of these is refused with a 422.
    """

    model_config = ConfigDict(extra='forbid')

    resolution: ResolutionChoice = Field(default='done', description='`done` or `wont_do`.')
    subtasks: SubtaskChoice = Field(
        default='done',
        description='What happens to the open subtasks: closed as `done`, closed as `wont_do`, or '
        'left open (`leave`).',
    )
    note: str | None = Field(
        default=None,
        description='A comment explaining the close, up to 10,000 characters; only with '
        '`resolution=wont_do`.',
    )


class ResolutionSchema(Schema):
    """Body for `POST /tasks/{number}/resolution`: how the closed task is now to be read, `done` or
    `wont_do`."""

    model_config = ConfigDict(extra='forbid')

    resolution: ResolutionChoice = Field(description='`done` or `wont_do`.')


class StateSchema(Schema):
    """Body for `POST /tasks/{number}/state`: the open task's new state, `todo`, `in_progress` or
    `on_hold`. `blocked` is refused with a 400: only blockers set it."""

    model_config = ConfigDict(extra='forbid')

    state: SettableState = Field(description='`todo`, `in_progress` or `on_hold`.')


class BlockerSuggestionSchema(Schema):
    """An open task that may be offered as a blocker of another."""

    number: int = Field(description="The task's number.")
    title: str = Field(description="The task's title.")


class BlockerSchema(Schema):
    """Body for `POST /tasks/{number}/blockers`: the number of the task that is to block it."""

    model_config = ConfigDict(extra='forbid')

    number: int = Field(description='The number of the task that blocks this one.')


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
