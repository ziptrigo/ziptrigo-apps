"""Schemas for the reminder endpoints (`/api/todo/tasks/{number}/reminders`, `/reminders/history`)
and the reminders a task carries."""

from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict


class ReminderSchema(Schema):
    """A reminder of a task: `minutes_before` its due time, `channel` `in_app`, `email` or `both`,
    `fire_at` the instant it is due and `sent_at` when it was delivered (or swept), else `null`."""

    id: UUID
    minutes_before: int
    channel: str
    fire_at: datetime
    sent_at: datetime | None


class ReminderCreateSchema(Schema):
    """Body for `POST /tasks/{number}/reminders` (and an item of `reminders` when creating a
    task). `channel` defaults to the account's default channel."""

    model_config = ConfigDict(extra='forbid')

    minutes_before: int
    channel: str | None = None


class ReminderUpdateSchema(Schema):
    """Body for `PATCH /tasks/{number}/reminders/{id}`: a new time and/or channel. A field left
    out, or `null`, is unchanged."""

    model_config = ConfigDict(extra='forbid')

    minutes_before: int | None = None
    channel: str | None = None


class DeliverySchema(Schema):
    """One delivery of a reminder through one channel (the history). `task` is the task's number,
    `null` once the task is deleted for good; `task_number` and `task_title` are copied at
    delivery. `note` is `unconfirmed`, `failed`, `skipped` or empty."""

    id: UUID
    task: int | None
    task_number: int
    task_title: str
    channel: str
    note: str
    fire_at: datetime
    sent_at: datetime


class DeliveryListSchema(Schema):
    count: int
    limit: int
    offset: int
    results: list[DeliverySchema]
