"""Schemas for the tag endpoints (`/api/notes/tags`)."""

from typing import Annotated
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field

from ..limits import MAX_REORDER_IDS


class NoteTagRefSchema(Schema):
    """A tag as a note carries it: enough to show and to filter by."""

    id: UUID
    name: str
    color: str


class NoteTagSchema(Schema):
    """One of the caller's tags. `note_count` counts the notes that have it, the Trash not
    included."""

    id: UUID
    name: str
    color: str
    description: str
    position: int
    note_count: int


class NoteTagCreateSchema(Schema):
    """Body for `POST /tags`. `color` is `#rrggbb` (empty: the default colour)."""

    model_config = ConfigDict(extra='forbid')

    name: str
    color: str = ''
    description: str = ''


class NoteTagUpdateSchema(Schema):
    """Body for `PATCH /tags/{id}`: only the fields present are applied. An explicit `null`
    clears the field (`description` to empty, `color` to the default colour; a `name` cannot be
    empty, so `null` there is a 400). A key that is not one of these is a 422."""

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    color: str | None = None
    description: str | None = None


class NoteTagOrderSchema(Schema):
    """Body for `POST /tags/reorder`: the tag ids in the new order (ids that aren't yours are
    ignored; at most 500, a longer list is a 422)."""

    ids: Annotated[list[UUID], Field(max_length=MAX_REORDER_IDS)]
