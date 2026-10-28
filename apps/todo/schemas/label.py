"""Schemas for the label endpoints (`/api/todo/labels`)."""

from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict


class LabelRefSchema(Schema):
    """A label as a task carries it: enough to show and to filter by."""

    id: UUID
    name: str
    color: str


class LabelSchema(Schema):
    """One of the caller's labels. `task_count` counts the tasks that have it, the Trash not
    included."""

    id: UUID
    name: str
    color: str
    description: str
    position: int
    task_count: int


class LabelCreateSchema(Schema):
    """Body for `POST /labels`. `color` is `#rrggbb` (empty: the default colour)."""

    model_config = ConfigDict(extra='forbid')

    name: str
    color: str = ''
    description: str = ''


class LabelUpdateSchema(Schema):
    """Body for `PATCH /labels/{id}`: only the fields present are applied. An explicit `null`
    clears the field (`description` to empty, `color` to the default colour; a `name` cannot be
    empty, so `null` there is a 400). A key that is not one of these is a 422."""

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    color: str | None = None
    description: str | None = None


class LabelOrderSchema(Schema):
    """Body for `POST /labels/reorder`: the label ids in the new order (ids that aren't yours are
    ignored)."""

    ids: list[UUID]
