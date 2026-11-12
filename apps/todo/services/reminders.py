"""Reminders, in-app, by email and by push (issues #182, #239).

A `Reminder` is `minutes_before` a task's due time (a date-only task is due at the owner's default
due time, `dates.due_at`). `Reminder.fire_at` is that instant in UTC; it is recomputed by
`reschedule` whenever the date, the time, the default due time or the owner's time zone changes.
`deliver_due` (the `todo.send_reminders` job, every minute) delivers what is due and records each
delivery as a `ReminderDelivery`, the history.

**Whole days are calendar days.** An offset that is a whole number of days (1440 minutes or a
multiple, so "1 day before" and "1 week before") is taken on the owner's wall clock: the due local
date minus the days, at the same local time, then converted to UTC. Across a clock change that
keeps "1 day before" at the same hour of the day, whereas 24 elapsed hours would land an hour off.
Every other offset (minutes and hours) is real elapsed time.

**Re-arming.** A date or time the *user* changes (`update_task`, `postpone_task`,
`reschedule_overdue`: `on_due_changed`) recomputes every reminder of the task, sent ones too, and
arms a sent one again (`sent_at` null) when its new time is ahead, so a postponed task reminds the
user for its new due time. The time-zone and default-due-time passes never do (`reschedule_user`
touches unsent reminders only): they move the same due moment, and re-arming there could fire a
reminder twice for it.

**Stale reminders.** A reminder added when its time has already passed fires at the next job run,
so one set a minute late is not lost -- but only if it is no older than `FIRE_WINDOW` (1 hour) by
then: the job never delivers older ones, it marks them sent and `skipped` (the sweep, which also
keeps the partial index on unsent reminders small), with a `ReminderDelivery` of note `skipped` in
the history when the task is open and its owner active -- unless the reminder was already due when
it was created (`fire_at <= created_at`), which was never ahead of the user and would only bury the
real deliveries: the task page still shows it as skipped, the history has no row. `add_reminder`
goes further and creates a reminder that is more than `LATE_GRACE` (24 hours) overdue already sent
and skipped (without a history row, by the same rule), so a long-dated edit ("1 day before" on a
task due in an hour) doesn't leave an unsent row behind. Between the two limits the job's rule
decides.

A completed or trashed task keeps its reminders, but the job skips them (leaving them unsent) and so
does it for an inactive user: restoring or uncompleting a task whose time is still ahead (or within
the window) makes them live again; the sweep marks the older ones sent.

**Held while muted** (issues #272, #273). A task in a muted state (`MUTED_STATES`: On hold, Blocked) is skipped by
`deliver_due` and left alone by `sweep_stale`, so its reminders stay unsent however long it stays
muted. When it leaves the state (`release_held`, called by `task_state.write_state`: `tasks.set_state` and the blockers' automatic changes) **one** reminder is
sent for the task, on the channels the held reminders used between them (each once: in-app, with
its push, and/or email), the history recording that one delivery; every held reminder is marked sent
(the delivered one as such, the others skipped). Held reminders of a task that is closed (closing resets the state) or trashed follow the
ordinary rules above: the job skips them and the sweep ends the old ones.

**Push.** An in-app reminder (channel `in_app` or `both`) is also pushed to the owner's devices
that have push on (`apps.core.services.push`, queued on commit), with two buttons: **Complete**
and **Snooze** (Chrome shows two at most; an iPhone shows none, and a tap on the notification
opens the task). The push is recorded as a `push` delivery holding the notification's id, which is
how `answer_push` (registered for `PUSH_KIND` in `apps.py`) finds the task when a button is
tapped. Snooze arms the reminder again `TodoAccount.snooze_minutes` from now (`snooze`), with
`snoozed_until` set so the passes that only move the same due moment leave it alone; a new due
date or time, or editing the reminder's time, replaces the snooze.
"""

import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Exists, F, OuterRef, QuerySet
from django.tasks import task as background_task
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import local_datetime, user_today, user_tz
from apps.core.datetime_format import PUBLIC, WEEKDAY_NAMES, format_date, format_time
from apps.core.models import PushAction, PushSubscription
from apps.core.services import notifications, push
from apps.core.services.email import send_email

from ..limits import MAX_REMINDER_MINUTES, MAX_REMINDERS_PER_TASK
from ..models import (
    MUTED_STATES,
    DeliveryChannel,
    EventChannel,
    Reminder,
    ReminderChannel,
    ReminderDelivery,
    SnoozeChoice,
    Task,
    TodoAccount,
)
from . import events
from .accounts import get_account
from .dates import due_at
from .references import render_markdown

logger = logging.getLogger(__name__)

#: A reminder created more than this far past its time is created already sent (no delivery).
LATE_GRACE = timedelta(hours=24)
#: The job delivers a reminder only while its time is at most this far in the past; older unsent
#: ones are marked sent without a delivery.
FIRE_WINDOW = timedelta(hours=1)
#: How many due reminders one job pass locks and delivers at a time.
BATCH = 200
#: The longest title in a notification and in an email subject.
NOTICE_TITLE_MAX = 120
SUBJECT_TITLE_MAX = 80

MINUTES_PER_DAY = 1440

NO_DATE_MESSAGE = 'Set a due date first.'
DUPLICATE_MESSAGE = 'There is already a reminder at that time.'
TOO_MANY_MESSAGE = f'A task can have at most {MAX_REMINDERS_PER_TASK} reminders.'
NOT_OPEN_MESSAGE = 'Only an open task has reminders.'
MINUTES_MESSAGE = 'Choose a reminder time between 0 minutes and 30 days before.'
CHANNEL_MESSAGE = 'Choose in-app, email or both.'
SNOOZE_MESSAGE = 'Choose 15 minutes, 1 hour, 8 hours or 1 day.'

#: The `kind` of a pushed reminder (`core.PushAction.kind`), and its two buttons.
PUSH_KIND = 'todo.reminder'
ACTION_COMPLETE = 'complete'
ACTION_SNOOZE = 'snooze'
#: The Snooze button's label for each `TodoAccount.snooze_minutes`.
SNOOZE_BUTTON = {
    SnoozeChoice.MIN_15: 'Snooze 15 min',
    SnoozeChoice.HOUR_1: 'Snooze 1 h',
    SnoozeChoice.HOURS_8: 'Snooze 8 h',
    SnoozeChoice.DAY_1: 'Snooze 1 day',
}


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


# -- Computing -----------------------------------------------------------------------------------


def fire_at_for(task: Task, minutes_before: int, account: TodoAccount) -> datetime | None:
    """The UTC instant a reminder `minutes_before` the task's due time is due. The due moment is
    the task's time, or the account's default due time for a date-only task, on the owner's wall
    clock (`dates.due_at`). A whole number of days (1440 minutes or more, evenly) is subtracted on
    that wall clock -- the same local time N calendar days earlier, so "1 day before" stays at the
    same hour across a clock change -- and anything else is real elapsed minutes. `None` for a task
    without a due date."""
    if task.due_date is None:
        return None
    due_time = task.due_time or account.default_due_time
    if minutes_before and minutes_before % MINUTES_PER_DAY == 0:
        earlier = task.due_date - timedelta(days=minutes_before // MINUTES_PER_DAY)
        return local_datetime(task.user, earlier, due_time)
    due = due_at(task, account)
    assert due is not None
    return due - timedelta(minutes=minutes_before)


def clean_minutes(value: object) -> int:
    """`value` as whole minutes, 0 to `MAX_REMINDER_MINUTES`."""
    try:
        minutes = int(value)  # ty: ignore[invalid-argument-type]
    except TypeError, ValueError:
        raise _error('minutes_before', MINUTES_MESSAGE) from None
    if not 0 <= minutes <= MAX_REMINDER_MINUTES:
        raise _error('minutes_before', MINUTES_MESSAGE)
    return minutes


def clean_channel(value: object) -> str:
    channel = str(value or '')
    if channel not in ReminderChannel.values:
        raise _error('channel', CHANNEL_MESSAGE)
    return channel


def describe_offset(minutes: int) -> str:
    """`At time of task`, `5 minutes before`, `1 hour before`, `2 days before`: the largest of
    minutes, hours and days that divides the offset evenly."""
    if minutes == 0:
        return 'At time of task'
    for size, name in ((MINUTES_PER_DAY, 'day'), (60, 'hour'), (1, 'minute')):
        if minutes % size == 0:
            count = minutes // size
            return f'{count} {name}{"" if count == 1 else "s"} before'
    raise AssertionError('unreachable: every number of minutes is a whole number of minutes')


def split_offset(minutes: int) -> tuple[int, str]:
    """`(amount, unit)` (`minutes`, `hours` or `days`) the edit form shows for an offset."""
    for size, unit in ((MINUTES_PER_DAY, 'days'), (60, 'hours')):
        if minutes and minutes % size == 0:
            return minutes // size, unit
    return minutes, 'minutes'


UNIT_MINUTES = {'minutes': 1, 'hours': 60, 'days': MINUTES_PER_DAY}


def due_label(task: Task, today=None) -> str:
    """What a notification and an email say is due: `Due today` for a date-only task due on the
    owner's today, else `Due Nov 5, 2026` or, with a time, `Due Nov 5, 2026, 9:05 PM` (the owner's
    wall clock, the very values stored). Always the public wording, whatever the owner's date and
    time format: a message is written by a job, with no request to take the formats from, and goes
    to a notification, a push and an email alike (issue #245)."""
    if task.due_date is None:
        return ''
    due = task.due_date
    if task.due_time is None:
        if due == (today or user_today(task.user)):
            return 'Due today'
        return f'Due {format_date(due, "full", PUBLIC)}'
    return f'Due {format_date(due, "full", PUBLIC)}, {format_time(task.due_time, PUBLIC)}'


# -- Creating and changing -----------------------------------------------------------------------


def _initial_sent_at(fire_at: datetime, now: datetime) -> datetime | None:
    """`None` (it will fire) unless the time is more than `LATE_GRACE` in the past, when the
    reminder starts already sent."""
    return now if fire_at < now - LATE_GRACE else None


def _create(
    task: Task, account: TodoAccount, minutes: int, channel: str, now: datetime
) -> Reminder:
    fire_at = fire_at_for(task, minutes, account)
    assert fire_at is not None  # callers check the due date
    sent_at = _initial_sent_at(fire_at, now)
    reminder = Reminder.objects.create(
        task=task,
        minutes_before=minutes,
        channel=channel,
        fire_at=fire_at,
        sent_at=sent_at,
        skipped=sent_at is not None,
    )
    # No history row for a reminder created already too old to fire: it was never ahead of the user
    # (see `_record_skipped`), and the task page shows it as skipped.
    return reminder


def _lock_open_task(task: Task) -> Task:
    """`task` re-read under its row lock (inside a transaction), which must be open."""
    current = Task.objects.select_for_update().filter(pk=task.pk).first()
    if current is None:
        raise _error(NON_FIELD_ERRORS, 'This task no longer exists.')
    if not current.is_open:
        raise _error(NON_FIELD_ERRORS, NOT_OPEN_MESSAGE)
    current.user = task.user  # already loaded: the zone and the default time need it
    return current


def add_reminder(
    task: Task, *, minutes_before: int, channel: str = ReminderChannel.IN_APP
) -> Reminder:
    """Add a reminder `minutes_before` the task's due time (the caller checks the task is the
    user's). The task must be open and have a due date; one reminder per `minutes_before`, at most
    `MAX_REMINDERS_PER_TASK`. The task row is locked, so racing adds can't pass the cap together.

    `sent_at` stays null even when `fire_at` is already past, so a reminder added a little late
    fires at the next job run; one more than `LATE_GRACE` overdue is created already sent (see the
    module docstring)."""
    minutes = clean_minutes(minutes_before)
    chosen = clean_channel(channel)
    account = get_account(task.user)
    now = timezone.now()
    with transaction.atomic():
        current = _lock_open_task(task)
        if current.due_date is None:
            raise _error(NON_FIELD_ERRORS, NO_DATE_MESSAGE)
        existing = Reminder.objects.filter(task=current)
        if existing.filter(minutes_before=minutes).exists():
            raise _error('minutes_before', DUPLICATE_MESSAGE)
        if existing.count() >= MAX_REMINDERS_PER_TASK:
            raise _error(NON_FIELD_ERRORS, TOO_MANY_MESSAGE)
        return _create(current, account, minutes, chosen, now)


def update_reminder(
    reminder: Reminder, *, minutes_before: int | None = None, channel: str | None = None
) -> Reminder:
    """Change a reminder's time and/or channel (`None` leaves a field). A new time recomputes
    `fire_at` and arms the reminder again (`sent_at` null, unless the new time is more than
    `LATE_GRACE` in the past); a channel alone leaves a sent reminder sent."""
    minutes = clean_minutes(minutes_before) if minutes_before is not None else None
    chosen = clean_channel(channel) if channel is not None else None
    task = reminder.task
    account = get_account(task.user)
    now = timezone.now()
    with transaction.atomic():
        current_task = _lock_open_task(task)
        current = Reminder.objects.select_for_update().filter(pk=reminder.pk).first()
        if current is None:
            raise _error(NON_FIELD_ERRORS, 'This reminder no longer exists.')
        fields: list[str] = []
        if chosen is not None and chosen != current.channel:
            current.channel = chosen
            fields.append('channel')
        if minutes is not None and minutes != current.minutes_before:
            if (
                Reminder.objects.filter(task=current_task, minutes_before=minutes)
                .exclude(pk=current.pk)
                .exists()
            ):
                raise _error('minutes_before', DUPLICATE_MESSAGE)
            fire_at = fire_at_for(current_task, minutes, account)
            if fire_at is None:
                raise _error(NON_FIELD_ERRORS, NO_DATE_MESSAGE)
            current.minutes_before = minutes
            current.fire_at = fire_at
            current.sent_at = _initial_sent_at(fire_at, now)
            current.skipped = current.sent_at is not None
            current.failures = 0
            current.snoozed_until = None
            fields += [
                'minutes_before',
                'fire_at',
                'sent_at',
                'skipped',
                'failures',
                'snoozed_until',
            ]
        if fields:
            current.save(update_fields=fields)
            if current.skipped and 'sent_at' in fields:
                current.task = current_task
                _record_skipped([current], now, edited=True)
    for name in ('minutes_before', 'channel', 'fire_at', 'sent_at', 'skipped'):
        setattr(reminder, name, getattr(current, name))
    return reminder


def delete_reminder(reminder: Reminder) -> None:
    """Remove a reminder (its history stays)."""
    with transaction.atomic():
        _lock_open_task(reminder.task)
        reminder.delete()


# -- Rescheduling --------------------------------------------------------------------------------


REFRESH_FIELDS = ['fire_at', 'sent_at', 'skipped', 'failures', 'snoozed_until']


def _refresh(
    reminders: Iterable[Reminder],
    account: TodoAccount,
    *,
    rearm: bool = False,
    now: datetime | None = None,
) -> int:
    """Recompute `fire_at` of `reminders` (each with its task, and the task's user, loaded) in one
    `bulk_update`, writing what changed. With `rearm`, a *sent* reminder whose new time is ahead of
    `now` is armed again (`sent_at` null, no longer skipped, its failure count reset); without it
    the callers pass unsent reminders only. A snoozed reminder waits for an instant the user
    chose, not one derived from the due time: without `rearm` (the same due moment, moved) it is
    left alone, with `rearm` (a new due moment) the snooze is dropped. Returns how many changed."""
    now = now or timezone.now()
    changed: list[Reminder] = []
    for reminder in reminders:
        if reminder.snoozed_until is not None and not rearm:
            continue
        fire_at = fire_at_for(reminder.task, reminder.minutes_before, account)
        if fire_at is None:
            continue
        dirty = fire_at != reminder.fire_at or reminder.snoozed_until is not None
        reminder.snoozed_until = None
        reminder.fire_at = fire_at
        if rearm and reminder.sent_at is not None and fire_at > now:
            reminder.sent_at = None
            reminder.skipped = False
            reminder.failures = 0
            dirty = True
        if dirty:
            changed.append(reminder)
    Reminder.objects.bulk_update(changed, REFRESH_FIELDS)
    return len(changed)


def reschedule(task: Task, account: TodoAccount | None = None, *, rearm: bool = False) -> int:
    """Recompute `fire_at` for the reminders of `task` that are not sent yet (call it after the
    task's date or time changed), or for all of them with `rearm`, which also arms a sent one again
    when its new time is ahead (see the module docstring: only for a change the user made).
    `fire_at` can't be empty, so a task whose due date was cleared loses its reminders instead (the
    web confirmation says so). Returns how many were changed or deleted."""
    if task.due_date is None:
        return Reminder.objects.filter(task=task).delete()[0]
    account = account or get_account(task.user)
    queryset = Reminder.objects.filter(task=task)
    if not rearm:
        queryset = queryset.filter(sent_at__isnull=True)
    rows = list(queryset)
    for reminder in rows:
        reminder.task = task
    return _refresh(rows, account, rearm=rearm)


def reminders_of_tasks(tasks: QuerySet) -> list[Reminder]:
    """Every reminder of the tasks in `tasks`, each with its task loaded: the first half of moving
    many tasks at once (`move_reminders` is the second), in one query."""
    return list(Reminder.objects.filter(task__in=tasks.values('pk')).select_related('task'))


def move_reminders(rows: list[Reminder], user: User, new_due_date: date) -> int:
    """After a bulk move of tasks to `new_due_date` (`reschedule_overdue`), recompute the
    reminders `reminders_of_tasks` loaded before the move, re-arming sent ones whose new time is
    ahead: one `bulk_update`, however many tasks. Returns how many changed."""
    for reminder in rows:
        reminder.task.user = user  # already loaded: the zone is needed for every one
        reminder.task.due_date = new_due_date
    return _refresh(rows, get_account(user), rearm=True)


def reschedule_user(user: User, *, untimed_only: bool = False) -> int:
    """Recompute `fire_at` of every unsent reminder of `user`'s tasks: after their time zone
    changed (all of them), or the default due time did (`untimed_only`: tasks with a date and no
    time, the only ones that use it). Never re-arms a sent reminder: the due moment did not change.
    Completed and trashed tasks are included, so restoring one later finds its reminders already
    right. Returns how many changed."""
    account = get_account(user)
    queryset = Reminder.objects.filter(
        task__user=user, task__due_date__isnull=False, sent_at__isnull=True
    ).select_related('task')
    if untimed_only:
        queryset = queryset.filter(task__due_time__isnull=True)
    total = 0
    batch: list[Reminder] = []
    for reminder in queryset.iterator(chunk_size=500):
        reminder.task.user = user
        batch.append(reminder)
        if len(batch) >= 500:
            total += _refresh(batch, account)
            batch = []
    return total + _refresh(batch, account)


def on_due_changed(task: Task, *, had_date: bool) -> None:
    """What a changed due date or time does to the reminders (call it after saving, for a change
    the user made): all are recomputed and a sent one whose new time is ahead is armed again, or
    they are deleted when the date was cleared; a task that just *gained* a date (it had none) gets
    the default reminder when it has none yet."""
    account = get_account(task.user)
    reschedule(task, account, rearm=True)
    if not had_date:
        apply_defaults(task, account)


@background_task(queue_name='default')
def reschedule_user_reminders(user_id: str, untimed_only: bool = False) -> None:
    """After a time zone change (`apps.py` connects `timezone_changed` to it) or a new default due
    time (`untimed_only`, `accounts.update_default_due_time`): every unsent reminder of the user is
    recomputed on their wall clock. Queued, not run in the request: a user can have thousands of
    tasks. Idempotent."""
    try:
        user = User.objects.get(pk=UUID(user_id))
    except User.DoesNotExist, ValueError:
        return
    reschedule_user(user, untimed_only=untimed_only)


# -- Defaults and recurrence ---------------------------------------------------------------------


def apply_defaults(task: Task, account: TodoAccount) -> Reminder | None:
    """Give an open task with a due date and no reminders the account's default one
    (`default_reminder_minutes`, `default_reminder_channel`), unless the account has none. Called by
    `create_task` for every source and when a task first gains a date."""
    if task.due_date is None or account.default_reminder_minutes is None or not task.is_open:
        return None
    if Reminder.objects.filter(task=task).exists():
        return None
    return _create(
        task,
        account,
        account.default_reminder_minutes,
        account.default_reminder_channel,
        timezone.now(),
    )


def copy_extras(from_task: Task, to_task: Task) -> None:
    """The next occurrence of a repeating task gets its reminders: `minutes_before` and `channel` of
    each, `fire_at` recomputed for the new date, `sent_at` null. It replaces whatever `create_task`
    gave the new task by default, so the occurrence has exactly the reminders its predecessor had
    (none, if the user removed them all)."""
    Reminder.objects.filter(task=to_task).delete()
    if to_task.due_date is None:
        return
    account = get_account(to_task.user)
    copies: list[Reminder] = []
    for reminder in Reminder.objects.filter(task=from_task):
        fire_at = fire_at_for(to_task, reminder.minutes_before, account)
        assert fire_at is not None
        copies.append(
            Reminder(
                task=to_task,
                minutes_before=reminder.minutes_before,
                channel=reminder.channel,
                fire_at=fire_at,
            )
        )
    Reminder.objects.bulk_create(copies)


def set_defaults(account: TodoAccount, *, minutes: int | None, channel: str) -> TodoAccount:
    """Save the default reminder (`minutes` before the due time, `None` for none) and its channel.
    Only tasks created (or first given a date) afterwards use it."""
    if minutes is not None:
        minutes = clean_minutes(minutes)
    account.default_reminder_minutes = minutes
    account.default_reminder_channel = clean_channel(channel)
    account.save(update_fields=['default_reminder_minutes', 'default_reminder_channel'])
    return account


def clean_snooze(value: object) -> int:
    """`value` as one of the `SnoozeChoice` minutes."""
    try:
        minutes = int(value)  # ty: ignore[invalid-argument-type]
    except TypeError, ValueError:
        raise _error('snooze_minutes', SNOOZE_MESSAGE) from None
    if minutes not in SnoozeChoice.values:
        raise _error('snooze_minutes', SNOOZE_MESSAGE)
    return minutes


def set_snooze(account: TodoAccount, minutes: int) -> TodoAccount:
    """Save how long the Snooze button of a pushed reminder waits."""
    account.snooze_minutes = clean_snooze(minutes)
    account.save(update_fields=['snooze_minutes'])
    return account


def task_reminders(task: Task) -> list[Reminder]:
    """The task's reminders, earliest first, each with `offset_text`, `amount` and `unit` for the
    page (the inline edit form shows the amount and unit)."""
    rows = list(Reminder.objects.filter(task=task).order_by('-minutes_before', 'created_at'))
    for row in rows:
        row.offset_text = describe_offset(row.minutes_before)
        row.amount, row.unit = split_offset(row.minutes_before)
    return rows


def task_history(task: Task, limit: int = 20) -> list[ReminderDelivery]:
    """The latest `limit` deliveries of the task's reminders, newest first."""
    return list(ReminderDelivery.objects.filter(task=task).order_by('-sent_at')[:limit])


def with_reminder_flags(queryset: QuerySet) -> QuerySet:
    """Annotate `has_reminders` (the bell on a row), without joining the reminders in."""
    return queryset.annotate(has_reminders=Exists(Reminder.objects.filter(task=OuterRef('pk'))))


# -- Delivering ----------------------------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + '…'


def _record(
    reminder: Reminder,
    *,
    channel: str,
    now: datetime,
    note: str = '',
    notification_id=None,
    fire_token: UUID | None = None,
    push_id: UUID | None = None,
) -> ReminderDelivery:
    task = reminder.task
    return ReminderDelivery.objects.create(
        reminder=reminder,
        task=task,
        user=task.user,
        task_number=task.number,
        task_title=task.title,
        channel=channel,
        note=note,
        sent_at=now,
        fire_at=reminder.fire_at,
        notification_id=notification_id,
        fire_token=fire_token,
        push_id=push_id,
    )


def _record_skipped(rows: Iterable[Reminder], now: datetime, *, edited: bool = False) -> None:
    """Write the history row of reminders that were not delivered because their time had passed
    (`note='skipped'`, in the first channel they would have used). Only for a task that is open
    and an active owner: for a completed or trashed task, or an inactive user, there was nothing to
    miss, and a row per reminder would only bury the history. Nor for a reminder that was already
    due when it was created (`fire_at <= created_at`: the default reminder of a task made for today
    after its time, say): it was never ahead of the user, and a row for every such task buried the
    deliveries that matter. A reminder armed again or moved ahead later has a `fire_at` after its
    `created_at`, so it is recorded, and so is one the user moved into the past by editing its time
    (`edited`). `rows` have their task and its user loaded."""
    deliveries = [
        ReminderDelivery(
            reminder=reminder,
            task=reminder.task,
            user=reminder.task.user,
            task_number=reminder.task.number,
            task_title=reminder.task.title,
            channel=(
                DeliveryChannel.EMAIL
                if reminder.channel == ReminderChannel.EMAIL
                else DeliveryChannel.IN_APP
            ),
            note=ReminderDelivery.NOTE_SKIPPED,
            sent_at=now,
            fire_at=reminder.fire_at,
        )
        for reminder in rows
        if (edited or reminder.fire_at > reminder.created_at)
        and reminder.task.is_open
        and reminder.task.user.is_active
        and reminder.task.user.status == User.STATUS_ACTIVE
    ]
    ReminderDelivery.objects.bulk_create(deliveries)


def _queue_email(reminder: Reminder, fire_token: UUID, now: datetime) -> None:
    """Queue the email of one firing once the surrounding transaction has committed. If queuing
    fails (the reminder is already marked sent by then) the failure is recorded in the history, so
    it doesn't vanish: `note='failed'`. `send_reminder_email` records every other outcome."""

    def _enqueue() -> None:
        try:
            send_reminder_email.enqueue(str(reminder.pk), str(fire_token))
        except Exception:
            logger.exception('Could not queue the email of reminder %s', reminder.pk)
            try:
                _record(
                    reminder,
                    channel=DeliveryChannel.EMAIL,
                    now=now,
                    note=ReminderDelivery.NOTE_FAILED,
                    fire_token=fire_token,
                )
            except Exception:
                logger.exception('Could not record the failed email of reminder %s', reminder.pk)

    transaction.on_commit(_enqueue)


def _deliver(
    reminder: Reminder, now: datetime, fire_token: UUID, channel: str | None = None
) -> None:
    """Deliver one claimed reminder through its channel(s) (`channel` overrides the reminder's own:
    the one reminder that stands for several held ones). In-app is written here, in the caller's
    transaction; an email is queued on commit (`send_reminder_email` records it)."""
    task_row = reminder.task
    channel = channel or reminder.channel
    if channel in (ReminderChannel.IN_APP, ReminderChannel.BOTH):
        notification = notifications.notify(
            task_row.user,
            source='todo',
            title=_truncate(task_row.title, NOTICE_TITLE_MAX),
            body=due_label(task_row),
            url=reverse('todo:task', args=[task_row.number]),
        )
        _record(
            reminder,
            channel=DeliveryChannel.IN_APP,
            now=now,
            notification_id=notification.pk,
            fire_token=fire_token,
        )
        _queue_push(reminder, now, fire_token)
    if channel in (ReminderChannel.EMAIL, ReminderChannel.BOTH):
        _queue_email(reminder, fire_token, now)


def _queue_push(reminder: Reminder, now: datetime, fire_token: UUID) -> None:
    """Push an in-app reminder to the owner's devices, when push is on and they turned it on for
    at least one device: queued on commit (`push.queue_push`, a network call per device), and
    recorded as a `push` delivery holding the notification's id, which the answer comes back
    with. The task's tag makes a new reminder of the same task replace the previous one."""
    task_row = reminder.task
    if not push.is_configured():
        return
    if not PushSubscription.objects.filter(user_id=task_row.user_id).exists():
        return
    account = get_account(task_row.user)
    push_id = push.queue_push(
        task_row.user,
        title=_truncate(task_row.title, NOTICE_TITLE_MAX),
        body=due_label(task_row),
        url=reverse('todo:task', args=[task_row.number]),
        actions=[
            {'action': ACTION_COMPLETE, 'title': 'Complete'},
            {
                'action': ACTION_SNOOZE,
                'title': SNOOZE_BUTTON.get(account.snooze_minutes, 'Snooze'),
            },
        ],
        tag=f'todo-task-{task_row.pk}',
        kind=PUSH_KIND,
    )
    _record(
        reminder,
        channel=DeliveryChannel.PUSH,
        now=now,
        fire_token=fire_token,
        push_id=push_id,
    )


def sweep_stale(now: datetime) -> int:
    """Mark unsent reminders more than `FIRE_WINDOW` overdue as sent and `skipped`, without
    delivering them, `BATCH` at a time under row locks (a row another run holds is left to it). The
    history gets a `skipped` row for each one whose task is open and owner active. Returns how many
    were swept."""
    cutoff = now - FIRE_WINDOW
    swept = 0
    while True:
        with transaction.atomic():
            batch = list(
                Reminder.objects.filter(sent_at__isnull=True, fire_at__lt=cutoff)
                # A muted open task holds its reminders however old they get (`release_held`).
                .exclude(
                    task__state__in=MUTED_STATES,
                    task__completed_at__isnull=True,
                    task__deleted_at__isnull=True,
                )
                .select_related('task', 'task__user')
                .select_for_update(skip_locked=True, of=('self',))
                .order_by('fire_at')[:BATCH]
            )
            if not batch:
                return swept
            Reminder.objects.filter(pk__in=[r.pk for r in batch], sent_at__isnull=True).update(
                sent_at=now, skipped=True
            )
            _record_skipped(batch, now)
            swept += len(batch)


def _note_failure(reminder: Reminder, exc: Exception) -> None:
    """A delivery raised (and its savepoint rolled back, claim included, so the reminder is tried
    again next run until the sweep ends it an hour after its time). The full traceback is logged
    for the first failure only; later ones are a one-line warning, so a reminder that always fails
    doesn't log a traceback every minute. The count (`Reminder.failures`) is stored, so it holds
    across runs and runners."""
    if reminder.failures == 0:
        logger.exception('Could not deliver reminder %s', reminder.pk)
    else:
        logger.warning(
            'Could not deliver reminder %s again (failure %d): %s: %s',
            reminder.pk,
            reminder.failures + 1,
            type(exc).__name__,
            exc,
        )
    try:
        Reminder.objects.filter(pk=reminder.pk).update(failures=F('failures') + 1)
    except Exception:
        logger.exception('Could not count the failure of reminder %s', reminder.pk)


def deliver_due(now: datetime | None = None) -> int:
    """The job body: deliver every reminder that is due (unsent, `fire_at` within the last
    `FIRE_WINDOW`) and whose task is open and owner active, in batches of `BATCH` under
    `select_for_update(skip_locked=True)`. Each is claimed with a conditional `UPDATE` on
    `sent_at IS NULL` (the compare-and-swap, so two runs never deliver one twice), then delivered
    in a savepoint of its own: one that fails is logged (`_note_failure`) and left unsent for the
    next run. Older unsent reminders are marked sent and skipped first (`sweep_stale`). Returns how
    many were delivered."""
    now = now or timezone.now()
    sweep_stale(now)
    delivered = 0
    failed: set[UUID] = set()
    while True:
        with transaction.atomic():
            batch = list(
                Reminder.objects.filter(
                    sent_at__isnull=True,
                    fire_at__lte=now,
                    task__completed_at__isnull=True,
                    task__deleted_at__isnull=True,
                    task__user__is_active=True,
                    task__user__status=User.STATUS_ACTIVE,
                )
                .exclude(task__state__in=MUTED_STATES)  # held until the task is unmuted
                .exclude(pk__in=failed)
                .select_related('task', 'task__user')
                .select_for_update(skip_locked=True, of=('self',))
                .order_by('fire_at')[:BATCH]
            )
            for reminder in batch:
                try:
                    fire_token = uuid4()
                    with transaction.atomic():
                        claimed = Reminder.objects.filter(
                            pk=reminder.pk, sent_at__isnull=True
                        ).update(
                            sent_at=now, skipped=False, fire_token=fire_token, snoozed_until=None
                        )
                        if not claimed:
                            continue
                        _deliver(reminder, now, fire_token)
                    delivered += 1
                except Exception as exc:
                    failed.add(reminder.pk)
                    _note_failure(reminder, exc)
        if not batch:
            return delivered


def has_held(task: Task, now: datetime | None = None) -> bool:
    """Whether the task has reminders waiting for it to leave a muted state (unsent and already
    due): what the task page's note about "one reminder for what you missed" depends on. One query,
    for the page only (a list row doesn't ask)."""
    return Reminder.objects.filter(
        task=task, sent_at__isnull=True, fire_at__lte=now or timezone.now()
    ).exists()


def release_held(task: Task, now: datetime | None = None) -> int:
    """Send the reminders a muted task held, now that it is To do or In progress again: **one**
    reminder for the task, on the union of the channels the held reminders used (each channel once:
    in-app with its push, and email), recorded as a delivery of the earliest held one. Every held
    reminder -- the unsent ones whose time has passed -- is marked sent, and skipped except the one that
    was delivered. `task` has its
    user loaded and is no longer muted; call it in the transaction that changed the state. A
    delivery that raises is rolled back (its own savepoint) and logged: the state change stands and
    the reminders stay unsent, for the job to deliver or sweep as it would any other. Returns how
    many reminders were held."""
    now = now or timezone.now()
    with transaction.atomic():
        held = list(
            Reminder.objects.select_for_update()
            .filter(task=task, sent_at__isnull=True, fire_at__lte=now)
            .order_by('fire_at', 'minutes_before')
        )
        if not held:
            return 0
        channels = {reminder.channel for reminder in held}
        in_app = bool(channels & {ReminderChannel.IN_APP, ReminderChannel.BOTH})
        email = bool(channels & {ReminderChannel.EMAIL, ReminderChannel.BOTH})
        channel = (
            ReminderChannel.BOTH
            if in_app and email
            else ReminderChannel.IN_APP
            if in_app
            else ReminderChannel.EMAIL
        )
        representative = held[0]
        fire_token = uuid4()
        try:
            with transaction.atomic():
                # The one that stands for the rest was delivered (it shows "Sent"); the others
                # were folded into it and show "Skipped".
                Reminder.objects.filter(
                    pk__in=[r.pk for r in held if r.pk != representative.pk], sent_at__isnull=True
                ).update(sent_at=now, skipped=True, snoozed_until=None)
                Reminder.objects.filter(pk=representative.pk).update(
                    sent_at=now, skipped=False, snoozed_until=None, fire_token=fire_token
                )
                representative.task = task
                representative.fire_token = fire_token
                _deliver(representative, now, fire_token, channel)
        except Exception:
            logger.exception('Could not release the held reminders of task %s', task.pk)
            return 0
        return len(held)


# -- The email -----------------------------------------------------------------------------------


@background_task(queue_name='default')
def send_reminder_email(reminder_id: str, fire_token: str) -> None:
    """Email one firing of a reminder to its task's owner, and record the delivery.

    `fire_token` is the reminder's `fire_token` at the claim that queued this task: a reminder that
    has been claimed again since (re-armed, then fired again) belongs to a newer firing and this
    one sends nothing, and an email row already recorded for the token is a duplicate run. A
    re-armed reminder therefore emails again, even at the same instant.

    Only to a *confirmed* address (changing the address through the API un-confirms the account but
    leaves sessions valid, and this mail quotes the user's task): otherwise nothing is sent and the
    delivery is recorded with `note='unconfirmed'`. The title and description are the user's own
    text, so both bodies are rendered from autoescaped templates and only the title (cut to
    `SUBJECT_TITLE_MAX`) is in the subject. A reminder deleted since it was queued, a task that is
    no longer open, an inactive owner, or an email already recorded for this very firing, send
    nothing."""
    try:
        token = UUID(fire_token)
        reminder = Reminder.objects.select_related('task', 'task__user').get(pk=UUID(reminder_id))
    except Reminder.DoesNotExist, ValueError:
        return
    task_row = reminder.task
    user = task_row.user
    if reminder.fire_token != token:
        return
    if not task_row.is_open or not user.is_active or user.status != User.STATUS_ACTIVE:
        return
    if ReminderDelivery.objects.filter(
        reminder=reminder, channel=DeliveryChannel.EMAIL, fire_token=token
    ).exists():
        return
    now = timezone.now()
    if not user.email or not user.email_confirmed:
        _record(
            reminder,
            channel=DeliveryChannel.EMAIL,
            now=now,
            note=ReminderDelivery.NOTE_UNCONFIRMED,
            fire_token=token,
        )
        return

    base = settings.BASE_URL.rstrip('/')
    link = f'{base}{reverse("todo:task", args=[task_row.number])}'
    context = {
        'title': task_row.title,
        'due': due_label(task_row),
        'zone': user_tz(user).key,
        'description': task_row.description,
        'description_html': render_markdown(task_row.description, None)
        if task_row.description
        else '',
        'link': link,
        'settings_link': f'{base}{reverse("todo:settings")}',
    }
    successes, _ = send_email(
        to=user.email,
        subject=f'Reminder: {_truncate(task_row.title, SUBJECT_TITLE_MAX)}',
        text_body=render_to_string('todo/emails/reminder.txt', context),
        html_body=render_to_string('todo/emails/reminder.html', context),
    )
    _record(
        reminder,
        channel=DeliveryChannel.EMAIL,
        now=now,
        note='' if successes else ReminderDelivery.NOTE_FAILED,
        fire_token=token,
    )


# -- Answers to a pushed reminder ----------------------------------------------------------------


def snooze(reminder: Reminder, now: datetime | None = None) -> datetime:
    """Arm `reminder` again `TodoAccount.snooze_minutes` from `now` (the Snooze button), whether
    or not it has been sent, and return when it will fire. The task must still be open
    (`ValidationError` otherwise). `snoozed_until` keeps the time-zone and default-due-time passes
    from moving it; a new due moment replaces it (`_refresh`)."""
    now = now or timezone.now()
    task = reminder.task
    account = get_account(task.user)
    until = now + timedelta(minutes=account.snooze_minutes)
    with transaction.atomic():
        _lock_open_task(task)
        updated = Reminder.objects.filter(pk=reminder.pk).update(
            fire_at=until, snoozed_until=until, sent_at=None, skipped=False, failures=0
        )
        if not updated:
            raise _error(NON_FIELD_ERRORS, 'This reminder no longer exists.')
    reminder.fire_at = reminder.snoozed_until = until
    reminder.sent_at = None
    reminder.skipped = False
    return until


def snoozed_label(user: User, until: datetime, now: datetime | None = None) -> str:
    """`9:05 PM` when `until` is on the owner's today, else `Wed Nov 4, 9:00 AM` (their wall clock),
    in the public wording whatever their format (the answer is shown by the phone's notification)."""
    zone = user_tz(user)
    local = until.astimezone(zone)
    today = (now or timezone.now()).astimezone(zone).date()
    clock = format_time(local.time(), PUBLIC)
    if local.date() == today:
        return clock
    day = format_date(local.date(), 'compact', PUBLIC, today=today)
    return f'{WEEKDAY_NAMES[local.weekday()][:3]} {day}, {clock}'


def answer_push(answer: PushAction) -> str | None:
    """What a tap on a pushed reminder's button does (`core`'s handler for `PUSH_KIND`, run in
    the transaction that records the answer), and what the device shows instead of the
    notification. Complete completes the task (and its open subtasks, as the website's Complete
    does once confirmed; a repeating task creates its next occurrence); Snooze arms the reminder
    again (`snooze`). A task that is gone, trashed or already completed, or a reminder removed
    since, changes nothing and says so."""
    delivery = (
        ReminderDelivery.objects.select_related('task', 'task__user', 'reminder')
        .filter(push_id=answer.push_id, user_id=answer.user_id, channel=DeliveryChannel.PUSH)
        .first()
    )
    task = delivery.task if delivery is not None else None
    if task is None:
        return 'This task no longer exists.'
    if task.is_trashed:
        return 'This task is in the Trash.'
    if task.completed_at is not None:
        return 'Already completed.'
    if answer.action == ACTION_COMPLETE:
        from .tasks import complete_task  # `tasks` imports this module

        subtasks = Task.objects.filter(
            parent=task, completed_at__isnull=True, deleted_at__isnull=True
        ).count()
        with events.channel(EventChannel.PUSH):
            complete_task(task)
        if subtasks:
            return f'Completed, with {subtasks} open subtask{"" if subtasks == 1 else "s"}.'
        return 'Completed.'
    if answer.action == ACTION_SNOOZE:
        reminder = delivery.reminder if delivery is not None else None
        if reminder is None:
            return 'This reminder was removed.'
        reminder.task = task
        try:
            until = snooze(reminder)
        except ValidationError as exc:
            return ' '.join(exc.messages)
        return f'Snoozed until {snoozed_label(task.user, until)}.'
    return None
