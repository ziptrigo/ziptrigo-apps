"""Reminders, in-app and by email (issue #182).

A `Reminder` is `minutes_before` a task's due time (a date-only task is due at the owner's default
due time, `dates.due_at`). `Reminder.fire_at` is that instant in UTC; it is recomputed by
`reschedule` whenever the date, the time, the default due time or the owner's time zone changes.
`deliver_due` (the `todo.send_reminders` job, every minute) delivers what is due and records each
delivery as a `ReminderDelivery`, the history.

**Stale reminders.** A reminder added when its time has already passed fires at the next job run,
so one set a minute late is not lost -- but only if it is no older than `FIRE_WINDOW` (1 hour) by
then: the job never delivers older ones, it marks them sent without a delivery (also to keep the
`(sent_at, fire_at)` index small). `add_reminder` goes further and creates a reminder that is more
than `LATE_GRACE` (24 hours) overdue already sent, so a long-dated edit ("1 day before" on a task
due in an hour) doesn't leave an unsent row behind. Between the two limits the job's rule decides.

A completed or trashed task keeps its reminders, but the job skips them (leaving them unsent) and so
does it for an inactive user: restoring or uncompleting a task whose time is still ahead (or within
the window) makes them live again; the sweep marks the older ones sent.
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.tasks import task as background_task
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today, user_tz
from apps.core.services import notifications
from apps.core.services.email import send_email
from apps.core.services.queue import enqueue_after_commit

from ..limits import MAX_REMINDER_MINUTES, MAX_REMINDERS_PER_TASK
from ..models import (
    DeliveryChannel,
    Reminder,
    ReminderChannel,
    ReminderDelivery,
    Task,
    TodoAccount,
)
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

NO_DATE_MESSAGE = 'Set a due date first.'
DUPLICATE_MESSAGE = 'There is already a reminder at that time.'
TOO_MANY_MESSAGE = f'A task can have at most {MAX_REMINDERS_PER_TASK} reminders.'
NOT_OPEN_MESSAGE = 'Only an open task has reminders.'
MINUTES_MESSAGE = 'Choose a reminder time between 0 minutes and 30 days before.'
CHANNEL_MESSAGE = 'Choose in-app, email or both.'


def _error(field: str, message: str) -> ValidationError:
    return ValidationError({field: message})


# -- Computing -----------------------------------------------------------------------------------


def fire_at_for(task: Task, minutes_before: int, account: TodoAccount) -> datetime | None:
    """The UTC instant a reminder `minutes_before` the task's due time is due: the due instant
    (`dates.due_at`: the task's time, or the account's default due time for a date-only task, on the
    owner's wall clock) minus the minutes. `None` for a task without a due date."""
    due = due_at(task, account)
    return None if due is None else due - timedelta(minutes=minutes_before)


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
    for size, name in ((1440, 'day'), (60, 'hour'), (1, 'minute')):
        if minutes % size == 0:
            count = minutes // size
            return f'{count} {name}{"" if count == 1 else "s"} before'
    raise AssertionError('unreachable: every number of minutes is a whole number of minutes')


def split_offset(minutes: int) -> tuple[int, str]:
    """`(amount, unit)` (`minutes`, `hours` or `days`) the edit form shows for an offset."""
    for size, unit in ((1440, 'days'), (60, 'hours')):
        if minutes and minutes % size == 0:
            return minutes // size, unit
    return minutes, 'minutes'


UNIT_MINUTES = {'minutes': 1, 'hours': 60, 'days': 1440}


def due_label(task: Task, today=None) -> str:
    """What a notification and an email say is due: `Due today` for a date-only task due on the
    owner's today, else `Due 5 Nov 2026` or, with a time, `Due 5 Nov 2026, 14:30` (the owner's
    wall clock, the very values stored)."""
    if task.due_date is None:
        return ''
    due = task.due_date
    if task.due_time is None:
        if due == (today or user_today(task.user)):
            return 'Due today'
        return f'Due {due.day} {due:%b} {due.year}'
    return f'Due {due.day} {due:%b} {due.year}, {task.due_time:%H:%M}'


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
    return Reminder.objects.create(
        task=task,
        minutes_before=minutes,
        channel=channel,
        fire_at=fire_at,
        sent_at=_initial_sent_at(fire_at, now),
    )


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
            fields += ['minutes_before', 'fire_at', 'sent_at']
        if fields:
            current.save(update_fields=fields)
    for name in ('minutes_before', 'channel', 'fire_at', 'sent_at'):
        setattr(reminder, name, getattr(current, name))
    return reminder


def delete_reminder(reminder: Reminder) -> None:
    """Remove a reminder (its history stays)."""
    with transaction.atomic():
        _lock_open_task(reminder.task)
        reminder.delete()


# -- Rescheduling --------------------------------------------------------------------------------


def _refresh(reminders: Iterable[Reminder], account: TodoAccount) -> int:
    """Recompute `fire_at` of `reminders` (each with its task loaded), writing what changed.
    Returns how many changed."""
    changed: list[Reminder] = []
    for reminder in reminders:
        fire_at = fire_at_for(reminder.task, reminder.minutes_before, account)
        if fire_at is not None and fire_at != reminder.fire_at:
            reminder.fire_at = fire_at
            changed.append(reminder)
    Reminder.objects.bulk_update(changed, ['fire_at'])
    return len(changed)


def reschedule(task: Task, account: TodoAccount | None = None) -> int:
    """Recompute `fire_at` for every reminder of `task` that is not sent yet (call it after the
    task's date or time changed). `fire_at` can't be empty, so a task whose due date was cleared
    loses its reminders instead (the web confirmation says so). Returns how many were changed or
    deleted."""
    if task.due_date is None:
        return Reminder.objects.filter(task=task).delete()[0]
    account = account or get_account(task.user)
    unsent = list(Reminder.objects.filter(task=task, sent_at__isnull=True))
    for reminder in unsent:
        reminder.task = task
    return _refresh(unsent, account)


def reschedule_user(user: User, *, untimed_only: bool = False) -> int:
    """Recompute `fire_at` of every unsent reminder of `user`'s tasks: after their time zone
    changed (all of them), or the default due time did (`untimed_only`: tasks with a date and no
    time, the only ones that use it). Completed and trashed tasks are included, so restoring one
    later finds its reminders already right. Returns how many changed."""
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
    """What a changed due date or time does to the reminders (call it after saving): the unsent ones
    are recomputed, or deleted when the date was cleared; a task that just *gained* a date (it had
    none) gets the default reminder when it has none yet."""
    account = get_account(task.user)
    reschedule(task, account)
    if not had_date:
        apply_defaults(task, account)


@background_task(queue_name='default')
def reschedule_user_reminders(user_id: str) -> None:
    """After a time zone change (`apps.py` connects `timezone_changed` to it): every unsent
    reminder of the user is recomputed on their new wall clock. Idempotent."""
    try:
        user = User.objects.get(pk=UUID(user_id))
    except User.DoesNotExist, ValueError:
        return
    reschedule_user(user)


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
    reminder: Reminder, *, channel: str, now: datetime, note: str = '', notification_id=None
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
    )


def _deliver(reminder: Reminder, now: datetime) -> None:
    """Deliver one claimed reminder through its channel(s). In-app is written here, in the
    caller's transaction; an email is queued on commit (`send_reminder_email` records it)."""
    task_row = reminder.task
    if reminder.channel in (ReminderChannel.IN_APP, ReminderChannel.BOTH):
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
        )
    if reminder.channel in (ReminderChannel.EMAIL, ReminderChannel.BOTH):
        enqueue_after_commit(send_reminder_email, str(reminder.pk))


def sweep_stale(now: datetime) -> int:
    """Mark unsent reminders more than `FIRE_WINDOW` overdue as sent, without delivering them."""
    return Reminder.objects.filter(sent_at__isnull=True, fire_at__lt=now - FIRE_WINDOW).update(
        sent_at=now
    )


def deliver_due(now: datetime | None = None) -> int:
    """The job body: deliver every reminder that is due (unsent, `fire_at` within the last
    `FIRE_WINDOW`) and whose task is open and owner active, in batches of `BATCH` under
    `select_for_update(skip_locked=True)`. Each is claimed with a conditional `UPDATE` on
    `sent_at IS NULL` (the compare-and-swap, so two runs never deliver one twice), then delivered
    in a savepoint of its own: one that fails is logged and left unsent for the next run. Older
    unsent reminders are marked sent without a delivery first (`sweep_stale`). Returns how many were
    delivered."""
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
                .exclude(pk__in=failed)
                .select_related('task', 'task__user')
                .select_for_update(skip_locked=True, of=('self',))
                .order_by('fire_at')[:BATCH]
            )
            for reminder in batch:
                try:
                    with transaction.atomic():
                        claimed = Reminder.objects.filter(
                            pk=reminder.pk, sent_at__isnull=True
                        ).update(sent_at=now)
                        if not claimed:
                            continue
                        _deliver(reminder, now)
                    delivered += 1
                except Exception:
                    failed.add(reminder.pk)
                    logger.exception('Could not deliver reminder %s', reminder.pk)
        if not batch:
            return delivered


# -- The email -----------------------------------------------------------------------------------


@background_task(queue_name='default')
def send_reminder_email(reminder_id: str) -> None:
    """Email a reminder to its task's owner, and record the delivery.

    Only to a *confirmed* address (changing the address through the API un-confirms the account but
    leaves sessions valid, and this mail quotes the user's task): otherwise nothing is sent and the
    delivery is recorded with `note='unconfirmed'`. The title and description are the user's own
    text, so both bodies are rendered from autoescaped templates and only the title (cut to
    `SUBJECT_TITLE_MAX`) is in the subject. A reminder deleted since it was queued, a task that is
    no longer open, an inactive owner, or an email already recorded for this very firing, send
    nothing."""
    try:
        reminder = Reminder.objects.select_related('task', 'task__user').get(pk=UUID(reminder_id))
    except Reminder.DoesNotExist, ValueError:
        return
    task_row = reminder.task
    user = task_row.user
    if not task_row.is_open or not user.is_active or user.status != User.STATUS_ACTIVE:
        return
    if ReminderDelivery.objects.filter(
        reminder=reminder, channel=DeliveryChannel.EMAIL, fire_at=reminder.fire_at
    ).exists():
        return
    now = timezone.now()
    if not user.email or not user.email_confirmed:
        _record(
            reminder,
            channel=DeliveryChannel.EMAIL,
            now=now,
            note=ReminderDelivery.NOTE_UNCONFIRMED,
        )
        return

    link = f'{settings.BASE_URL.rstrip("/")}{reverse("todo:task", args=[task_row.number])}'
    context = {
        'title': task_row.title,
        'due': due_label(task_row),
        'zone': user_tz(user).key,
        'description': task_row.description,
        'description_html': render_markdown(task_row.description, None)
        if task_row.description
        else '',
        'link': link,
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
    )
