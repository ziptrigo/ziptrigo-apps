"""Schemas for the settings, inbox and parse endpoints (`/api/todo/settings`, `/inbox`, `/parse`)."""

from datetime import datetime, time
from typing import Literal
from uuid import UUID

from ninja import Schema
from pydantic import ConfigDict, Field


class SettingsSchema(Schema):
    """The account's settings, and (read-only) its usage.

    `default_due_time` is the time a task with a date and no time is treated as due.
    `default_reminder_minutes` (`null`: none) and `default_reminder_channel` are the reminder a
    new task with a date gets. `snooze_minutes` (15, 60, 480 or 1440) is how long the Snooze button
    of a pushed reminder waits. `muted_overdue` is where an overdue task that is On hold or Blocked
    shows on Today: `dimmed` (at the bottom of the Overdue group, dimmed; the default), `hidden`
    (not on Today at all) or `section` (in a collapsed "On hold & blocked" section at the bottom of
    Today). A muted task due today stays in Today's list whatever this says. `sender_allowlist` is who may add tasks by email (exact addresses
    and `*@domain`; an empty list accepts anyone).

    Read-only: `tasks_created_this_month` (in the account's time zone) against
    `free_tasks_per_month`, `task_price` (credits per task beyond the free ones),
    `creation_blocked` (a repeating task is waiting to be paid for), `stored_bytes` of attachments,
    `storage_credits_per_day` (what storing them costs a day, a decimal string) and
    `storage_frozen` (storage could not be paid for: uploads and downloads are refused) and
    `credits_balance`, the user's credit balance (the shared balance, not only To-do's: what a new
    task beyond the free ones is paid from). Also read-only, the attachment limits
    (`max_attachment_bytes`, `max_attachments_per_task`), so a client can refuse a file before
    uploading it.
    """

    default_due_time: time
    default_reminder_minutes: int | None
    default_reminder_channel: str
    snooze_minutes: int
    muted_overdue: Literal['dimmed', 'hidden', 'section']
    sender_allowlist: list[str]
    tasks_created_this_month: int
    free_tasks_per_month: int
    task_price: int
    creation_blocked: bool
    stored_bytes: int
    storage_credits_per_day: str
    storage_frozen: bool
    credits_balance: int = Field(description="The user's current credit balance.")
    max_attachment_bytes: int = Field(
        description='The largest file, in bytes, that can be attached to a task or a comment.'
    )
    max_attachments_per_task: int = Field(
        description='The most files a task can hold. Uploads still pending and comment files '
        'not yet saved count toward it too.'
    )


class SettingsUpdateSchema(Schema):
    """Body for `PATCH /settings`; only the fields present are applied, and they are saved together
    or not at all. An explicit `null` clears: `default_reminder_minutes` to no default reminder,
    `sender_allowlist` to accept anyone. `default_due_time`, `default_reminder_channel`,
    `snooze_minutes` and `muted_overdue` (`dimmed`, `hidden` or `section`; any other value is a 400)
    cannot be empty, so `null` there is a 400. A key that is not one of these is a 422."""

    model_config = ConfigDict(extra='forbid')

    default_due_time: time | None = None
    default_reminder_minutes: int | None = None
    default_reminder_channel: str | None = None
    snooze_minutes: int | None = None
    muted_overdue: str | None = None
    sender_allowlist: list[str] | None = None


InboundStatus = Literal[
    'accepted',
    'rejected_sender',
    'rejected_spam',
    'rejected_virus',
    'rejected_credits',
    'rejected_too_large',
    'rejected_task_limit',
    'no_content',
]


class RecentEmailSchema(Schema):
    """One email that reached the address. `status` is `accepted` (it created `task`) or why it did
    not: `rejected_sender` (not on the accepted-senders list, or not authenticated as one of
    them), `rejected_spam`, `rejected_virus`, `rejected_credits` (not enough credits for a task),
    `rejected_too_large` (every file over the size limit), `rejected_task_limit` (too many open
    tasks) or `no_content` (nothing to add). `note` is a short remark (for instance `1 of 3 files
    skipped: too large`), often empty. `task` is the number of the task it created, `null` when it
    created none or the task has been deleted."""

    id: UUID = Field(description='The email row id.')
    from_address: str = Field(description='The From address, as the sender wrote it.')
    sender_authenticated: bool = Field(
        description='Whether the mail system authenticated the From address.'
    )
    subject: str = Field(description='The subject, as received (cut to 500 characters).')
    received_at: datetime | None = Field(description='When the mail system received it.')
    status: InboundStatus = Field(description='The outcome, see above.')
    note: str = Field(description='A short remark about what was done; often empty.')
    task: int | None = Field(description='The number of the task it created, or `null`.')


class InboxSchema(Schema):
    """The address tasks can be emailed to (subject: title, body: description, attachments:
    attachments), who may send to it and the 20 emails that reached it most recently
    (`recent_emails`, newest first; what `GET /inbox` shows, as the Settings page does)."""

    address: str
    sender_allowlist: list[str]
    recent_emails: list[RecentEmailSchema] = Field(
        description='The latest 20 emails received, accepted or not, newest first.'
    )
