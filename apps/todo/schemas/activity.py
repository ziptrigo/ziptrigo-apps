"""Schemas for a task's activity log (`GET /api/todo/tasks/{number}/activity`)."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from ninja import Schema
from pydantic import Field

from ..models import EventChannel, EventKind

#: The kinds and channels as an `enum` in the OpenAPI document (a plain string in the response, so a
#: kind added later is not a validation error for a client built against an older spec).
KindName = Annotated[str, Field(json_schema_extra={'enum': list(EventKind.values)})]
ChannelName = Annotated[str, Field(json_schema_extra={'enum': list(EventChannel.values)})]


class ActivityEventSchema(Schema):
    """One entry of a task's log: something that changed.

    `text` is the entry written as a sentence (`Due date changed from Nov 5, 2026, 3:00 PM to Nov 7,
    2026, 9:00 AM`; always the public wording, whatever the account's date and time format, and in
    English). `kind` and `data` are the same entry as data, for a client that wants to write its
    own: `data` is a JSON object whose keys depend on the `kind` and which holds the values
    themselves (old and new titles, task numbers and titles, label and file names), so the entry
    still reads right after what it mentions is gone. `automatic` is true when nobody asked for the
    change in that moment (a task unblocked by a blocker closing, the next occurrence of a repeating
    task, what closing a parent does to its subtasks, a task made from an email). `channel` is the
    entry point that made the change: `web`, `api`, `email`, `push` (a phone notification's button)
    or `system` (a job).

    The `data` of each `kind`: `created` (`source`, `parent`, `previous`, `rrule`), `state` (`from`,
    `to`), `closed` (`resolution`, `via`, `subtasks`, `subtasks_resolution`), `reopened` (`to`,
    `resolution`), `resolution` (`from`, `to`), `title` and `priority` (`from`, `to`), `due`
    (`from_date`, `from_time`, `to_date`, `to_time`), `labels` (`added`, `removed`), `repeat`
    (`from`, `to`, `mode`, `reason`), `description` (nothing), `checklist` (`checked`, `text`),
    `blocker_added` and `blocker_removed` (`number`, `title`), `blocked` (`from`, `blockers`),
    `unblocked` (`blockers` with each `reason`), `subtask_added`, `subtask_removed` and
    `subtask_closed` (`number`, `title`, ...), `moved` (`parent`), `attachment_added` and
    `attachment_removed` (`filename`, `comment`), `trashed` and `restored` (`via`).
    """

    id: UUID = Field(description="The entry's id.")
    kind: KindName = Field(description='What happened.')
    text: str = Field(description='The entry as a sentence, in the public wording.')
    data: dict[str, Any] = Field(description='The entry as data; its keys depend on `kind`.')
    automatic: bool = Field(description='Nobody asked for the change in that moment.')
    channel: ChannelName = Field(description='The entry point that made the change.')
    created_at: datetime = Field(description='When it happened.')


class ActivityPageSchema(Schema):
    """A page of a task's log, **newest first**. `next` is the cursor of the following (older)
    page, to send as `before`, or `null` on the last page. A cursor is an opaque keyset position
    (the time and id of the last entry), so entries written while paging never repeat or skip. It
    is base64url text (`A-Z a-z 0-9 - _`, no padding), safe in a query string as it is."""

    results: list[ActivityEventSchema] = Field(description='The entries, newest first.')
    next: str | None = Field(
        description='The cursor of the next, older page (send it as `before`); `null` on the last.'
    )
