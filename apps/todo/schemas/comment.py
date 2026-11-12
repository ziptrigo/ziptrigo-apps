"""Schemas for the comment endpoints (`/api/todo/tasks/{number}/comments`)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from .attachment import CommentAttachmentSchema


class CommentSchema(Schema):
    """A comment. `body` is the Markdown and `body_html` the rendered, sanitised HTML (`#1234` and
    task URLs linked). A deleted comment has `deleted_at` set and **no body** (`null`) unless
    `?include_deleted_bodies=1` asked for it. `edited_at` is `null` until the first edit and
    `revision_count` counts the edits. `attachments` are the comment's files (a deleted comment
    lists none unless `?include_deleted_bodies=1` asked for it)."""

    id: UUID
    task: int
    body: str | None
    body_html: str | None
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None
    revision_count: int
    attachments: list[CommentAttachmentSchema] = Field(
        description="The comment's files, oldest first."
    )


#: At most this many ids in one request (a task holds 20 files by default).
MAX_FILE_IDS = 100
FileIds = Annotated[list[str], Field(max_length=MAX_FILE_IDS)]


class CommentBodySchema(Schema):
    """Body for adding a comment (1 to 10,000 characters), with the files it carries.

    `attachment_ids` are comment files already uploaded (`POST /tasks/{number}/attachments` with
    `for_comment: true`, then `complete`) for this task: all of them are attached, or the comment
    is refused with a 400 keyed `attachment_ids` (one that is not an uploaded, unattached comment
    file of this task, or is still uploading)."""

    model_config = ConfigDict(extra='forbid')

    body: str
    attachment_ids: FileIds = Field(
        default=[], description='Uploaded comment files to attach (all or none).'
    )


class CommentEditSchema(CommentBodySchema):
    """Body for editing a comment: the text (required: a comment still needs text; the same text
    changes nothing and leaves no revision), `attachment_ids` to add (as when adding) and
    `remove_attachment_ids`, files of this comment to delete (ids that are not its files are
    ignored). Changing only the files is not an edit of the text: no revision, no `edited_at`."""

    remove_attachment_ids: FileIds = Field(
        default=[], description="Ids of this comment's files to delete."
    )


class RevisionSchema(Schema):
    """What a comment said before an edit replaced it, and when."""

    body: str
    body_html: str
    replaced_at: datetime
