"""Schemas for the saved-filter endpoints (`/api/notes/filters`)."""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field, field_validator

from ..limits import MAX_SAVED_FILTERS

#: At most this many tag ids in one filter: a user can have 200 tags (the default cap) and `none`.
MAX_FILTER_TAGS = 201


class NoteFilterParamsSchema(Schema):
    """What a saved filter selects (the same criteria as the query of `GET /notes`): search text
    `q` in `mode` `glob` or `regex`, a `scope` (`active`, `archived` or `all`: the Trash is never
    searched), `tags` (tag ids, and `none` for notes without a tag; a note with any of them
    matches), `shared` (`yes`: the note has a share link that is active and works; `no`: it does
    not; `null`: either), an `updated` range (`any`, `today`, `last_7_days`, `last_30_days` or
    `custom` with `updated_from` / `updated_to`, inclusive days in your time zone, either may be
    left out; relative ranges are resolved on every run) and a `sort` (`updated`, `created`,
    `title` or `manual`) with its `dir` (`asc` or `desc`; `null`: the sort's own default, newest
    first for `updated` and `created`). A tag id that isn't yours is dropped, as is one deleted
    later."""

    model_config = ConfigDict(extra='forbid')

    q: str = ''
    mode: Literal['glob', 'regex'] = 'glob'
    scope: Literal['active', 'archived', 'all'] = 'active'
    tags: Annotated[list[str], Field(max_length=MAX_FILTER_TAGS)] = []
    shared: Literal['yes', 'no'] | None = None
    updated: Literal['any', 'today', 'last_7_days', 'last_30_days', 'custom'] = 'any'
    updated_from: date | None = None
    updated_to: date | None = None
    sort: Literal['updated', 'created', 'title', 'manual'] = 'updated'
    dir: Literal['asc', 'desc'] | None = None

    @field_validator('tags')
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        for item in value:
            if item != 'none':
                UUID(item)  # a ValueError is the 422
        return value

    def to_params(self) -> dict[str, Any]:
        """The dict `services.saved_filters` takes."""
        data = self.model_dump(mode='json')
        data['shared'] = data['shared'] or ''
        data['dir'] = data['dir'] or ''
        return data

    @classmethod
    def from_stored(cls, params: Any) -> 'NoteFilterParamsSchema':
        """The schema of a `FilterParams` (what a saved filter holds)."""
        data = params.to_dict()
        data['shared'] = data.get('shared') or None
        data['dir'] = data.get('dir') or None
        return cls.model_validate(data)


class NoteFilterSchema(Schema):
    """A saved filter. `params` is what it selects (see `NoteFilterParamsSchema`) and `summary` a short
    description of it in the public wording (`@work · shared · last 7 days · "deploy*"`)."""

    id: UUID
    name: str
    position: int
    params: NoteFilterParamsSchema
    summary: str
    created_at: datetime


class NoteFilterCreateSchema(Schema):
    model_config = ConfigDict(extra='forbid')

    name: str
    params: NoteFilterParamsSchema = NoteFilterParamsSchema()


class NoteFilterUpdateSchema(Schema):
    """Body for `PATCH /filters/{id}`: a new `name` and/or new `params` (replacing the old ones as
    a whole). A field left out is unchanged; neither can be cleared (an explicit `null` is a 400)."""

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    params: NoteFilterParamsSchema | None = None


class NoteFilterOrderSchema(Schema):
    """Body for `POST /filters/reorder`: the filter ids in the new order (at most 50, the most saved
    filters one user can have; a longer list is a 422)."""

    model_config = ConfigDict(extra='forbid')

    ids: Annotated[list[UUID], Field(max_length=MAX_SAVED_FILTERS)]
