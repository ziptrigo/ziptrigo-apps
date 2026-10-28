"""Schemas for the settings, inbox and parse endpoints (`/api/todo/settings`, `/inbox`, `/parse`)."""

from datetime import time

from ninja import Schema
from pydantic import ConfigDict


class SettingsSchema(Schema):
    """The account's settings, and (read-only) its usage.

    `default_due_time` is the time a task with a date and no time is treated as due.
    `default_reminder_minutes` (`null`: none) and `default_reminder_channel` are the reminder a
    new task with a date gets. `sender_allowlist` is who may add tasks by email (exact addresses and
    `*@domain`; an empty list accepts anyone).

    Read-only: `tasks_created_this_month` (in the account's time zone) against
    `free_tasks_per_month`, `task_price` (credits per task beyond the free ones),
    `creation_blocked` (a repeating task is waiting to be paid for), `stored_bytes` of attachments,
    `storage_credits_per_day` (what storing them costs a day, a decimal string) and
    `storage_frozen` (storage could not be paid for: uploads and downloads are refused).
    """

    default_due_time: time
    default_reminder_minutes: int | None
    default_reminder_channel: str
    sender_allowlist: list[str]
    tasks_created_this_month: int
    free_tasks_per_month: int
    task_price: int
    creation_blocked: bool
    stored_bytes: int
    storage_credits_per_day: str
    storage_frozen: bool


class SettingsUpdateSchema(Schema):
    """Body for `PATCH /settings`; only the fields present are applied, and they are saved together
    or not at all. An explicit `null` clears: `default_reminder_minutes` to no default reminder,
    `sender_allowlist` to accept anyone. `default_due_time` and `default_reminder_channel` cannot
    be empty, so `null` there is a 400. A key that is not one of these is a 422."""

    model_config = ConfigDict(extra='forbid')

    default_due_time: time | None = None
    default_reminder_minutes: int | None = None
    default_reminder_channel: str | None = None
    sender_allowlist: list[str] | None = None


class InboxSchema(Schema):
    """The address tasks can be emailed to (subject: title, body: description, attachments:
    attachments) and who may send to it."""

    address: str
    sender_allowlist: list[str]
