"""Schemas for the saved-filter endpoints (`/api/todo/filters`)."""

from collections.abc import Collection
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field, field_validator

from ..limits import MAX_SAVED_FILTERS
from ..services.dates import SORTS
from ..services.filters import DATE_RANGES, MAX_LABELS, PRIORITIES
from ..services.search import MODES


def _one_of(value: str, allowed: Collection[str], name: str) -> str:
    if value not in allowed:
        raise ValueError(f'{name} must be one of: {", ".join(allowed)}.')
    return value


class FilterParamsSchema(Schema):
    """What a saved filter selects (the same as the query of `GET /tasks`): search text `q` in
    `mode` `glob` or `regex`, `include_completed`, `priorities` (0 to 3), `labels` (label ids, and
    `none` for tasks without one), a `date_range` (`any`, `overdue`, `today`, `next_7_days`,
    `next_30_days`, `no_date` or `custom` with `date_from` / `date_to`) and a `sort`. A label id
    that isn't yours is dropped, as is one deleted later."""

    model_config = ConfigDict(extra='forbid')

    q: str = ''
    mode: str = 'glob'
    include_completed: bool = False
    priorities: list[int] = []
    labels: list[str] = []
    date_range: str = 'any'
    date_from: date | None = None
    date_to: date | None = None
    sort: str = 'date'

    @field_validator('mode')
    @classmethod
    def _mode(cls, value: str) -> str:
        return _one_of(value, MODES, 'mode')

    @field_validator('date_range')
    @classmethod
    def _date_range(cls, value: str) -> str:
        return _one_of(value, tuple(DATE_RANGES), 'date_range')

    @field_validator('sort')
    @classmethod
    def _sort(cls, value: str) -> str:
        return _one_of(value, tuple(SORTS), 'sort')

    @field_validator('priorities')
    @classmethod
    def _priorities(cls, value: list[int]) -> list[int]:
        if any(priority not in PRIORITIES for priority in value):
            raise ValueError('priorities must be between 0 and 3.')
        return value

    @field_validator('labels')
    @classmethod
    def _labels(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_LABELS:
            raise ValueError(f'At most {MAX_LABELS} labels.')
        for item in value:
            if item != 'none':
                UUID(item)  # a ValueError is the 422
        return value

    def to_params(self) -> dict[str, Any]:
        """The dict `services.saved_filters` takes."""
        return self.model_dump(mode='json')


class FilterSchema(Schema):
    """A saved filter. `params` is the stored form (see `FilterParamsSchema`) and `summary` a short
    description of it (`priority 1 · @home · next 7 days`)."""

    id: UUID
    name: str
    position: int
    params: dict[str, Any]
    summary: str
    created_at: datetime


class FilterCreateSchema(Schema):
    model_config = ConfigDict(extra='forbid')

    name: str
    params: FilterParamsSchema = FilterParamsSchema()


class FilterUpdateSchema(Schema):
    """Body for `PATCH /filters/{id}`: a new `name` and/or new `params` (replacing the old ones as
    a whole). A field left out is unchanged."""

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    params: FilterParamsSchema | None = None


class FilterOrderSchema(Schema):
    """Body for `POST /filters/reorder`: the filter ids in the new order (at most 50, the most saved
    filters one user can have; a longer list is a 422)."""

    ids: Annotated[list[UUID], Field(max_length=MAX_SAVED_FILTERS)]
