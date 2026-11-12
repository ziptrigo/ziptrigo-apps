"""Schemas for the saved-filter endpoints (`/api/todo/filters`)."""

from collections.abc import Collection
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field, field_validator, model_validator

from ..limits import MAX_SAVED_FILTERS
from ..services.dates import SORTS
from ..services.filters import DATE_RANGES, MAX_LABELS, PRIORITIES
from ..services.search import MODES


def _one_of(value: str, allowed: Collection[str], name: str) -> str:
    if value not in allowed:
        raise ValueError(f'{name} must be one of: {", ".join(allowed)}.')
    return value


class FilterParamsSchema(Schema):
    """What a saved filter selects (the same criteria as the query of `GET /tasks`, under the
    names a saved filter has always used): search text `q` in `mode` `glob` or `regex`,
    `include_closed` (closed tasks too), `resolution` (`done` or `wont_do`: which closed tasks
    `include_closed` lists; `null` for both; only with `include_closed`, else a 422), `states`
    (`todo`, `in_progress`, `blocked`, `on_hold`: an open task in any of them matches; closed tasks
    are not narrowed by it), `priorities` (0 to 3), `labels` (label ids, and `none` for tasks
    without one), a `date_range` (`any`, `overdue`, `today`, `next_7_days`, `next_30_days`,
    `no_date` or `custom` with `date_from` / `date_to`) and a `sort`. A label id that isn't yours is
    dropped, as is one deleted later. `overdue` leaves out On hold and Blocked tasks, which are
    never overdue.

    A saved filter made on the website can carry `states` and `resolution` too; they are read and
    written here like the rest."""

    model_config = ConfigDict(extra='forbid')

    q: str = ''
    mode: str = 'glob'
    include_closed: bool = Field(default=False, description='List closed tasks too.')
    resolution: Literal['done', 'wont_do'] | None = Field(
        default=None,
        description='Only the closed tasks closed that way; needs `include_closed`.',
    )
    states: list[Literal['todo', 'in_progress', 'blocked', 'on_hold']] = Field(
        default=[], description='An open task in any of these states matches.'
    )
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

    @model_validator(mode='after')
    def _resolution_needs_closed(self) -> 'FilterParamsSchema':
        if self.resolution is not None and not self.include_closed:
            raise ValueError('resolution applies only with include_closed.')
        return self

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
        """The dict `services.saved_filters` takes (its key for the closed switch is the old
        one)."""
        data = self.model_dump(mode='json')
        data['include_completed'] = data.pop('include_closed')
        data['resolution'] = data['resolution'] or ''
        return data

    @classmethod
    def from_stored(cls, params: Any) -> 'FilterParamsSchema':
        """The schema of a `FilterParams` (what a saved filter holds)."""
        data = params.to_dict()
        data['include_closed'] = data.pop('include_completed')
        data['resolution'] = data.get('resolution') or None
        data.setdefault('states', [])
        return cls.model_validate(data)


class FilterSchema(Schema):
    """A saved filter. `params` is what it selects (see `FilterParamsSchema`) and `summary` a short
    description of it in the public wording (`priority 1 · state on hold · @home · next 7 days`)."""

    id: UUID
    name: str
    position: int
    params: FilterParamsSchema
    summary: str
    created_at: datetime
    dropped_labels: int = Field(
        description=(
            'How many labels this filter was saved with that have since been deleted. They no '
            'longer count in `params`, so a filter whose only criterion was one now selects every '
            'open task; the website says "N labels it used were deleted". It is 0 again once the '
            "filter's `params` are updated (renaming alone does not reset it)."
        )
    )


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
