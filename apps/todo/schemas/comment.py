"""Schemas for the comment endpoints (`/api/todo/tasks/{number}/comments`)."""

from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict


class CommentSchema(Schema):
    """A comment. `body` is the Markdown and `body_html` the rendered, sanitised HTML (`#1234` and
    task URLs linked). A deleted comment has `deleted_at` set and **no body** (`null`) unless
    `?include_deleted_bodies=1` asked for it. `edited_at` is `null` until the first edit and
    `revision_count` counts the edits."""

    id: UUID
    task: int
    body: str | None
    body_html: str | None
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None
    revision_count: int


class CommentBodySchema(Schema):
    """Body for adding or editing a comment (1 to 10,000 characters)."""

    model_config = ConfigDict(extra='forbid')

    body: str


class RevisionSchema(Schema):
    """What a comment said before an edit replaced it, and when."""

    body: str
    body_html: str
    replaced_at: datetime
