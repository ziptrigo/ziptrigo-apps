"""Schemas for a note's version history (`/api/notes/notes/{id}/versions`)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field


class NoteVersionListItemSchema(Schema):
    """One entry of a note's history, without the text (read it with
    `GET /notes/{id}/versions/{version_id}`).

    `editor_kind` is who saved it: `owner`, `user` (another signed-in user, through a share link)
    or `anonymous` (someone who was not signed in, through an editable share link). `editor_name`
    is the account's name for `owner` and `user` and `null` for `anonymous`. `via_link` says it
    was saved through a share link. A run of saves by the same editor within ten minutes is one
    version, overwritten in place: `created_at` is when it was first written, `updated_at` the last
    save folded into it.
    """

    id: UUID
    number: int
    title: str
    editor_kind: Literal['owner', 'user', 'anonymous']
    editor_name: str | None
    via_link: bool
    created_at: datetime
    updated_at: datetime


class NoteVersionSchema(NoteVersionListItemSchema):
    """One version of a note: its title and content **after** that save."""

    content: str = Field(description='The note content as that save left it.')
