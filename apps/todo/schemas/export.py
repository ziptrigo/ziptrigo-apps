"""Schemas for the file export endpoints (`/api/todo/exports`, issue #326): the quote, the export
and its zip files, and the download link of one zip file. The twin of
`apps.receipts.schemas.export`, without the layout (To-do has one: `<Project>/<number> - <title>/`)
and with a date range in place of the receipts filters.

Every class name starts with `TodoExport`: the OpenAPI document keys a component by class name
alone, so a name another app also uses would silently replace it.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field


class TodoExportRequestSchema(Schema):
    """Body for `POST /exports/quote`: which tasks' files to pack, by the date they were
    **created** (the account page's "Task created" select). Every state and every subtask counts;
    the Trash never does. Only files on a task, or on a comment that is not deleted, are packed.

    - `date_range`: `any` (every task), `last_15_days`, `last_30_days`, `this_month`,
      `this_quarter`, `this_year` (resolved against today in your time zone) or `custom`. Left
      out, it is `custom` when a date is sent and `any` otherwise.
    - `date_from` / `date_to`: `YYYY-MM-DD`, inclusive, read in your time zone; either may be
      left out for no bound. Only with `custom` (or no `date_range`): dates sent with any other
      range are a 400, so a request can never cover more than it says. A malformed date, or one
      after `date_to`, is a 400 too.
    """

    model_config = ConfigDict(extra='forbid')

    date_range: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class TodoExportCreateSchema(TodoExportRequestSchema):
    """Body for `POST /exports`: the quote's fields and `expected_cost`.

    `expected_cost` is the total in credits the client confirmed (the quote's `cost`). If what
    the export costs now is different (files were added or removed, or the price changed) the
    answer is a 409 naming the real total and nothing is charged or created. Left out, the export
    is created whatever it costs, for a client with no one to ask.
    """

    expected_cost: int | None = Field(default=None, ge=0)


class TodoExportQuoteSchema(Schema):
    """What an export of this range would be. `cost` is `file_count x price_per_file` credits
    (`0` is free). `affordable` is `balance >= cost`; `frozen` says the account's storage is
    paused (an export would be refused with a 402). `retention_days` is how long the zip files
    stay downloadable after they are ready; `part_bytes` is about how big each zip file is."""

    file_count: int
    total_bytes: int
    part_count: int
    price_per_file: int
    cost: int
    balance: int
    affordable: bool
    frozen: bool
    retention_days: int
    part_bytes: int


class TodoExportPartSchema(Schema):
    """One zip file of an export. The zip files are independent (each opens on its own);
    `tasks.xlsx` (the tasks and their comments) is in number 1. `built` is false while it is still
    being written (`size_bytes` is 0 until then); `filename` is the name it downloads as."""

    number: int
    filename: str
    size_bytes: int
    file_count: int
    built: bool


class TodoExportFilterInfoSchema(Schema):
    """What the export was made from: `summary` is short lines for display (`Task created: ...`;
    empty for every task) and `params` the range that was used (the body's keys)."""

    summary: list[str]
    params: dict[str, Any]


class TodoExportSchema(Schema):
    """One export, as every export endpoint returns it.

    `status` is `building` (the zip files are being written: poll until it changes), `ready`
    (download the parts until `expires_at`), `failed` (it could not be built and every credit was
    refunded) or `expired` (past its retention; the files are gone). A deleted export is a 404.

    `charged_credits` was spent when the export was created; `refunded_credits` is given back for
    a failed export (all of it) or for files that had vanished by the time they were zipped
    (`skipped_count`, left out of the zip files). `file_count` is the number planned, so the zip
    files hold `file_count - skipped_count` once ready. `parts` is empty for a failed or expired
    export (its rows are gone). `ready_at` and `expires_at` are `null` until it is ready.
    """

    id: UUID
    status: Literal['building', 'ready', 'failed', 'expired']
    filters: TodoExportFilterInfoSchema
    file_count: int
    skipped_count: int
    total_bytes: int
    part_count: int
    price_per_file: int
    charged_credits: int
    refunded_credits: int
    parts: list[TodoExportPartSchema]
    created_at: datetime
    ready_at: datetime | None
    expires_at: datetime | None


class TodoExportListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[TodoExportSchema]


class TodoExportPartUrlSchema(Schema):
    """A short-lived presigned URL for one zip file: `GET` it (no `Authorization` header) within
    `expires_in` seconds; it downloads as `filename`. Never cache it; ask again."""

    url: str
    filename: str
    size_bytes: int
    expires_in: int
