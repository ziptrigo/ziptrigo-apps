"""Repeating tasks (issue #179): the rule subset, its description, the next date and what
completing an occurrence does.

A repeating task is a chain of ordinary `Task` rows (each billed, numbered, completed and searched
like any task) that share one `Recurrence`. Two modes:

* **fixed**: the next occurrence is computed from the current occurrence's *scheduled* date
  (`Recurrence.anchor_date`), so rent due on the 1st stays on the 1st even when it is paid late or
  postponed (postponing never touches the anchor);
* **from completion**: it is computed from the day the user completed the current one, in their
  time zone (tires rotated a week late push every later rotation a week).

**Supported rules** (`parse_rule`): `FREQ` `DAILY|WEEKLY|MONTHLY|YEARLY` (required); `INTERVAL`
1..365; `BYDAY` (weekday codes, with an ordinal `-1`, `1`..`4` for `MONTHLY` only: `1MO`, `-1FR`);
`BYMONTHDAY` (1..31 or -1); `BYMONTH` (1..12); `COUNT` (1..1000) or `UNTIL` (`YYYYMMDD`), not
both. `WKST` is ignored. Anything else is refused. The canonical form orders the parts `FREQ`,
`INTERVAL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH`, `COUNT`/`UNTIL` and leaves out `INTERVAL=1` and
`WKST`.

**Month lengths.** An explicit `BYMONTHDAY` beyond a month's length is skipped by `dateutil`, not
clamped: `BYMONTHDAY=31` has no occurrence in February, April, June, September or November. `-1` is
the last day of the month, whatever its length. A monthly or yearly rule with *no day of its own*
(no `BYMONTHDAY`, no `BYDAY`) repeats on the day of the month the schedule was set on and is
**clamped** to a short month's last day: 31 Jan, 28 Feb, 31 Mar, 30 Apr, 31 May (`Recurrence.
anchor_day` keeps the intended day once the anchor itself has been clamped); a yearly rule on 29 Feb
falls on 28 Feb in other years.

**COUNT** is not left to `dateutil`: a count would restart at every computed date (each call starts
a new series at `after`). `Recurrence.occurrences_created` counts the occurrences made, and
`next_date` ignores `COUNT`.
"""

import logging
import re
from calendar import monthrange
from datetime import date, datetime, time

from dateutil.rrule import rrulestr
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today
from apps.core.services import notifications

from ..models import Recurrence, RecurrenceMode, Task, TaskSource

logger = logging.getLogger(__name__)

MAX_LENGTH = 255
MAX_INTERVAL = 365
MAX_COUNT = 1000

UNSUPPORTED = 'Unsupported repeat rule.'
NO_UPCOMING = 'This repeat rule has no upcoming date.'

FREQUENCIES = ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY')
WEEKDAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')
WEEKDAY_NAMES = dict(
    zip(WEEKDAYS, ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'))
)
ORDINAL_WORDS = {1: 'first', 2: 'second', 3: 'third', 4: 'fourth', -1: 'last'}
MONTH_NAMES = (
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
)
_ALLOWED_PARTS = {
    'FREQ',
    'INTERVAL',
    'BYDAY',
    'BYMONTHDAY',
    'BYMONTH',
    'COUNT',
    'UNTIL',
    'WKST',  # ignored
}
_BYDAY_TOKEN = re.compile(r'^(-1|[1-4])?(MO|TU|WE|TH|FR|SA|SU)$')
_NUMBER = re.compile(r'^-?\d{1,4}$')
_UNTIL = re.compile(r'^\d{8}$')


def _unsupported() -> ValidationError:
    return ValidationError(UNSUPPORTED)


# -- The rule ------------------------------------------------------------------------------------


def _split(value: str) -> dict[str, str]:
    """`KEY=VALUE;...` as a dict with upper-case keys; a repeated or malformed part is refused."""
    parts: dict[str, str] = {}
    for segment in value.split(';'):
        segment = segment.strip()
        if not segment:
            continue
        key, separator, raw = segment.partition('=')
        key = key.strip().upper()
        if not separator or not raw.strip() or key in parts:
            raise _unsupported()
        parts[key] = raw.strip().upper()
    return parts


def _integers(raw: str, low: int, high: int, *, allow: tuple[int, ...] = ()) -> list[int]:
    numbers: list[int] = []
    for item in raw.split(','):
        item = item.strip()
        if not _NUMBER.match(item):
            raise _unsupported()
        number = int(item)
        if not (low <= number <= high or number in allow):
            raise _unsupported()
        numbers.append(number)
    return sorted(set(numbers))


def _day_key(token: str) -> tuple[int, int]:
    match = _BYDAY_TOKEN.match(token)
    assert match is not None
    return WEEKDAYS.index(match.group(2)), int(match.group(1) or 0)


def _single(raw: str, low: int, high: int) -> int:
    numbers = _integers(raw, low, high)
    if len(numbers) != 1 or ',' in raw:
        raise _unsupported()
    return numbers[0]


def parse_rule(value: str, today: date) -> str:
    """Validate `value` (an `RRULE` property value, no `DTSTART`) and return its canonical form.

    Raises `ValidationError('Unsupported repeat rule.')` for anything outside the subset in the
    module docstring, and a different message for a rule that is too long or never occurs again.
    `today` is the user's today (`user_today`), not UTC's: a rule is accepted while it still has a
    date on or after it, so an `UNTIL` of the user's own today is fine at 23:30 in Chicago.
    """
    value = str(value or '').strip()
    if value.upper().startswith('RRULE:'):
        value = value[len('RRULE:') :]
    if len(value) > MAX_LENGTH:
        raise ValidationError(f'The repeat rule is too long (at most {MAX_LENGTH} characters).')
    parts = _split(value)
    if not set(parts) <= _ALLOWED_PARTS:
        raise _unsupported()
    freq = parts.get('FREQ')
    if freq not in FREQUENCIES:
        raise _unsupported()

    canonical = [f'FREQ={freq}']
    if 'INTERVAL' in parts:
        interval = _single(parts['INTERVAL'], 1, MAX_INTERVAL)
        if interval > 1:
            canonical.append(f'INTERVAL={interval}')
    if 'BYDAY' in parts:
        tokens = {token.strip() for token in parts['BYDAY'].split(',')}
        if not all(_BYDAY_TOKEN.match(token) for token in tokens):
            raise _unsupported()
        if freq != 'MONTHLY' and any(token[0] in '-1234' for token in tokens):
            raise _unsupported()
        canonical.append('BYDAY=' + ','.join(sorted(tokens, key=_day_key)))
    if 'BYMONTHDAY' in parts:
        days = _integers(parts['BYMONTHDAY'], 1, 31, allow=(-1,))
        days = [day for day in days if day > 0] + [day for day in days if day < 0]
        canonical.append('BYMONTHDAY=' + ','.join(str(day) for day in days))
    if 'BYMONTH' in parts:
        canonical.append('BYMONTH=' + ','.join(str(m) for m in _integers(parts['BYMONTH'], 1, 12)))
    if 'COUNT' in parts and 'UNTIL' in parts:
        raise _unsupported()
    if 'COUNT' in parts:
        canonical.append(f'COUNT={_single(parts["COUNT"], 1, MAX_COUNT)}')
    if 'UNTIL' in parts:
        if not _UNTIL.match(parts['UNTIL']):
            raise _unsupported()
        try:
            datetime.strptime(parts['UNTIL'], '%Y%m%d')
        except ValueError:
            raise _unsupported() from None
        canonical.append(f'UNTIL={parts["UNTIL"]}')

    result = ';'.join(canonical)
    # Build it and read one occurrence: `dateutil` has the last word on what it accepts, and a rule
    # that can never occur again (an `UNTIL` in the past, February 30th) is no use as a repeat.
    try:
        first = _iterate(result, today, keep_count=True, inclusive=True)
    except ValueError, TypeError, OverflowError, IndexError:
        raise _unsupported() from None
    if first is None:
        raise ValidationError(NO_UPCOMING)
    return result


def _parts(rrule: str) -> list[str]:
    return [part for part in rrule.split(';') if part]


def _is_dayless(rrule: str) -> bool:
    """A monthly or yearly rule that names no day of its own (no `BYMONTHDAY`, no `BYDAY`): it
    falls on the day of the month it is computed from."""
    keys = {part.partition('=')[0] for part in _parts(rrule)}
    freq = next((p[len('FREQ=') :] for p in _parts(rrule) if p.startswith('FREQ=')), '')
    return freq in ('MONTHLY', 'YEARLY') and not keys & {'BYMONTHDAY', 'BYDAY'}


def _iterate(
    rrule: str,
    after: date,
    *,
    keep_count: bool = False,
    inclusive: bool = False,
    day: int | None = None,
) -> date | None:
    """The first date strictly after `after` (`inclusive`: on or after) that `rrule` yields, for
    the series that starts at `after`.

    `day` is the day of the month a day-less monthly/yearly rule means (default: `after`'s). Over
    28 it is clamped to a short month's last day instead of skipping the month: the rule is run on
    the 1st of each period (`BYMONTHDAY=1`, from the 1st of `after`'s month, with `BYMONTH` the
    start month for a yearly one) and each
    result moved to `min(day, month length)`; an `UNTIL` is then checked against the moved date.
    """
    if not keep_count:
        rrule = ';'.join(part for part in _parts(rrule) if not part.startswith('COUNT='))
    start_at = datetime.combine(after, time())
    day = day or after.day
    if day <= 28 or not _is_dayless(rrule):
        found = rrulestr('RRULE:' + rrule, dtstart=start_at).after(start_at, inc=inclusive)
        return found.date() if found is not None else None

    # From the 1st of `after`'s month (the same period as `after` itself, so `INTERVAL` still
    # counts from it): this month's own date is a candidate too.
    start_at = datetime.combine(after.replace(day=1), time())
    parts = _parts(rrule) + ['BYMONTHDAY=1']
    if 'FREQ=YEARLY' in parts and not any(part.startswith('BYMONTH=') for part in parts):
        parts.append(f'BYMONTH={after.month}')
    until = next((p[len('UNTIL=') :] for p in parts if p.startswith('UNTIL=')), None)
    last = datetime.strptime(until, '%Y%m%d').date() if until else None
    for found in rrulestr('RRULE:' + ';'.join(parts), dtstart=start_at):
        moved = found.date().replace(day=min(day, monthrange(found.year, found.month)[1]))
        if last is not None and moved > last:
            return None
        if moved > after or (inclusive and moved == after):
            return moved
    return None


def next_date(rrule: str, after: date, day: int | None = None) -> date | None:
    """The first date after `after` the rule yields, or `None` when it is exhausted (`UNTIL`).

    `COUNT` is ignored here: it is tracked by `Recurrence.occurrences_created` (see the module
    docstring). A `BYMONTHDAY` beyond the month's length skips that month; `-1` is its last day. A
    day-less monthly/yearly rule means `day` (default: `after`'s day of the month), clamped to the
    month's length.
    """
    return _iterate(rrule, after, day=day)


def count_of(rrule: str) -> int | None:
    """`COUNT` of a (canonical) rule, or `None`."""
    for part in rrule.split(';'):
        if part.startswith('COUNT='):
            return int(part[len('COUNT=') :])
    return None


# -- Describing ----------------------------------------------------------------------------------


def _ordinal_day(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return f'{number}{suffix}'


def _every(interval: int, unit: str) -> str:
    return f'Every {unit}' if interval == 1 else f'Every {interval} {unit}s'


def describe(rrule: str) -> str:
    """A sentence for the UI: `Every 2 weeks on Monday, Wednesday`, `Every month on the last day`,
    `Every year on 3 March, 5 times`. A combination without a sentence is `Custom repeat (<rule>)`.
    """
    custom = f'Custom repeat ({rrule})'
    try:
        parts = _split(rrule)
    except ValidationError:
        return custom
    freq = parts.get('FREQ')
    if freq not in FREQUENCIES or not set(parts) <= _ALLOWED_PARTS - {'WKST'}:
        return custom
    try:
        interval = int(parts.get('INTERVAL', '1'))
    except ValueError:
        return custom
    byday = [token for token in parts.get('BYDAY', '').split(',') if token]
    bymonthday = [token for token in parts.get('BYMONTHDAY', '').split(',') if token]
    bymonth = [token for token in parts.get('BYMONTH', '').split(',') if token]
    unit = {'DAILY': 'day', 'WEEKLY': 'week', 'MONTHLY': 'month', 'YEARLY': 'year'}[freq]
    text = _every(interval, unit)

    if freq == 'DAILY' and not (byday or bymonthday or bymonth):
        pass
    elif freq == 'WEEKLY' and not (bymonthday or bymonth):
        if byday:
            if not all(token in WEEKDAY_NAMES for token in byday):
                return custom
            text += ' on ' + ', '.join(WEEKDAY_NAMES[token] for token in byday)
    elif freq == 'MONTHLY' and not bymonth and not (byday and bymonthday):
        if bymonthday:
            if len(bymonthday) != 1:
                return custom
            day = int(bymonthday[0])
            text += ' on the last day' if day == -1 else f' on the {_ordinal_day(day)}'
        elif byday:
            match = _BYDAY_TOKEN.match(byday[0]) if len(byday) == 1 else None
            if match is None or match.group(1) is None:
                return custom
            text += f' on the {ORDINAL_WORDS[int(match.group(1))]} {WEEKDAY_NAMES[match.group(2)]}'
    elif freq == 'YEARLY' and not byday:
        if bymonth or bymonthday:
            if len(bymonth) != 1 or len(bymonthday) != 1 or int(bymonthday[0]) < 1:
                return custom
            text += f' on {int(bymonthday[0])} {MONTH_NAMES[int(bymonth[0]) - 1]}'
    else:
        return custom

    if 'COUNT' in parts:
        count = int(parts['COUNT'])
        text += f', {count} time' + ('' if count == 1 else 's')
    elif 'UNTIL' in parts:
        try:
            until = datetime.strptime(parts['UNTIL'], '%Y%m%d')
        except ValueError:
            return custom
        text += f', until {until.day} {until.strftime("%b")} {until.year}'
    return text


def mode_label(mode: str) -> str:
    return RecurrenceMode(mode).label if mode in RecurrenceMode.values else mode


# -- Cleaning ------------------------------------------------------------------------------------


def clean_rule(value: object, today: date) -> str:
    """`parse_rule`, with its refusal keyed to the `rrule` field (`today`: the user's today)."""
    try:
        return parse_rule(str(value or ''), today)
    except ValidationError as exc:
        raise ValidationError({'rrule': exc.messages}) from None


def clean_mode(value: object) -> str:
    mode = str(value or '').strip() or RecurrenceMode.FIXED
    if mode not in RecurrenceMode.values:
        raise ValidationError({'recurrence_mode': 'Choose a fixed schedule or from completion.'})
    return mode


def first_date(rrule: str, today: date) -> date:
    """The first date on or after `today` the rule yields (a task given a rule but no date).

    The series starts at `today` and includes it, so `BYMONTHDAY=1` gives the next 1st, a weekly
    `BYDAY=WE` on a Wednesday gives today, and a rule with no day of its own (`INTERVAL=3` months)
    gives today. (`next_date(rule, today - 1 day)` would start that last kind from yesterday.)"""
    first = _iterate(rrule, today, inclusive=True)
    if first is None:
        raise ValidationError({'rrule': NO_UPCOMING})
    return first


# -- Starting, changing, stopping ----------------------------------------------------------------


def start(user: User, rrule: str, mode: str, anchor: date) -> Recurrence:
    """A new `Recurrence` for a task about to be created (or given a rule); call it inside the
    caller's transaction. `rrule` and `mode` are already cleaned."""
    return Recurrence.objects.create(
        user=user, rrule=rrule, mode=mode, anchor_date=anchor, anchor_day=anchor.day
    )


def change(task: Task, rrule: str, mode: str, anchor: date) -> Recurrence:
    """Give `task` this rule: the running `Recurrence` is edited in place (under its row lock, so a
    completion at the same moment sees either the old rule or the new one), else a new one is made
    (also when the task's earlier one had ended). Editing the schedule sets the anchor to the
    task's date and the occurrence count starts again from 1: the task being edited is the first
    occurrence of the changed series, as a new `Recurrence` starts at 1, so a `COUNT` means the same
    whether it was set at creation or by an edit. Call it inside the
    caller's transaction. The caller sets the task's own `recurrence_anchor` to `anchor`: that is
    what makes the edited task the one whose completion creates the next occurrence (an older open
    occurrence of the same rule no longer does)."""
    active = task.active_recurrence
    if active is None:
        return start(task.user, rrule, mode, anchor)
    locked = Recurrence.objects.select_for_update().get(pk=active.pk)
    locked.rrule, locked.mode, locked.anchor_date = rrule, mode, anchor
    locked.anchor_day, locked.occurrences_created = anchor.day, 1
    locked.save(update_fields=['rrule', 'mode', 'anchor_date', 'anchor_day', 'occurrences_created'])
    return locked


def stop(task: Task) -> bool:
    """Stop repeating: the rule is ended, the task stays. Returns whether a running rule was
    ended (stopping twice is a no-op)."""
    if task.recurrence_id is None:
        return False
    now = timezone.now()
    ended = Recurrence.objects.filter(pk=task.recurrence_id, ended_at__isnull=True).update(
        ended_at=now
    )
    if ended and task.recurrence is not None:
        task.recurrence.ended_at = now
    return bool(ended)


# -- Completing ----------------------------------------------------------------------------------


def copy_extras(from_task: Task, to_task: Task) -> None:
    """Hook for what else an occurrence carries to the next one. The reminders sub-issue (#182)
    copies a task's reminders here; until then it does nothing."""


def on_completed(task: Task) -> Task | None:
    """What completing an occurrence does (`tasks.complete_task` calls it in its transaction, once
    `completed_at` is set): create the next occurrence, or end the rule.

    **An occurrence spawns its successor at most once.** The `Recurrence` row is locked first, and
    the next occurrence is created only while the anchor the task was scheduled for
    (`Task.recurrence_anchor`) is still the recurrence's `anchor_date`. Creating the successor moves
    the anchor on, so completing the same occurrence again (after an uncomplete), completing an
    older open occurrence, or two tabs completing at once, find the anchors different and create
    (and charge) nothing. The next date is counted from the anchor (fixed) or from the user's today
    (from completion). It is created with `allow_unpaid=True`: completing a repeating task always
    succeeds, and an occurrence the balance can't cover is owed (`services.billing`). Open subtasks
    are copied the same way. Returns the new occurrence, if one was made.

    If the occurrence can't be created (the open-task cap), it is retried without the copied
    subtasks, and the user is told in-app either way (`_notify`); when even that fails the
    recurrence and anchor stay as they were, so uncompleting and completing the task once there is
    room tries again.
    """
    from . import tasks as task_service

    if task.recurrence_id is None or task.recurrence is None:
        return None
    user = task.user
    with transaction.atomic():
        recurrence = Recurrence.objects.select_for_update().filter(pk=task.recurrence_id).first()
        if recurrence is None or recurrence.ended_at is not None:
            return None
        if task.recurrence_anchor != recurrence.anchor_date:
            return None
        count = count_of(recurrence.rrule)
        if recurrence.mode == RecurrenceMode.FIXED:
            nxt = next_date(
                recurrence.rrule,
                recurrence.anchor_date,
                recurrence.anchor_day or recurrence.anchor_date.day,
            )
        else:
            nxt = next_date(recurrence.rrule, user_today(user))
        if nxt is None or (count is not None and recurrence.occurrences_created >= count):
            ended_at = timezone.now()
            Recurrence.objects.filter(pk=recurrence.pk).update(ended_at=ended_at)
            task.recurrence.ended_at = ended_at  # the caller's copy reflects it
            return None
        created, dropped_subtasks = _create_with_fallback(task, recurrence, nxt, task_service)
        _notify(task, recurrence, created, dropped_subtasks)
        if created is None:
            return None
        recurrence.anchor_date = nxt
        recurrence.occurrences_created += 1
        recurrence.save(update_fields=['anchor_date', 'occurrences_created'])
    return created


def _create_with_fallback(
    task: Task, recurrence: Recurrence, due: date, task_service
) -> tuple[Task | None, bool]:
    """The next occurrence with its copied subtasks; when that is refused, once more without them.
    Returns `(occurrence or None, whether the subtasks were left out)`. Each attempt is a savepoint,
    so a refusal undoes the partial copy and its charges."""
    for with_subtasks in (True, False):
        try:
            with transaction.atomic():
                created = _create_occurrence(
                    task, recurrence, due, task_service, with_subtasks=with_subtasks
                )
            return created, not with_subtasks
        except ValidationError as exc:
            logger.warning(
                'Task %s: the next occurrence could not be created%s: %s',
                task.pk,
                '' if with_subtasks else ' even without its subtasks',
                '; '.join(exc.messages),
            )
            if with_subtasks and not _has_open_subtasks(task):
                break  # nothing to leave out: the same refusal again
    return None, False


def _has_open_subtasks(task: Task) -> bool:
    return Task.objects.filter(
        parent=task, completed_at__isnull=True, deleted_at__isnull=True
    ).exists()


def failure_key(recurrence: Recurrence) -> str:
    """The `key` of the in-app notice about a next occurrence that could not be made (or made in
    full) for this recurrence."""
    return f'todo_recurrence_failed:{recurrence.id}'


def _notify(
    task: Task, recurrence: Recurrence, created: Task | None, dropped_subtasks: bool
) -> None:
    """Tell the user in-app when the next occurrence was not created, or was created without its
    subtasks; otherwise clear an earlier notice (the repeat works again). One notice per
    recurrence (`failure_key`), replaced each time. Never raises: it runs in its own savepoint, and
    telling the user must not undo or fail the completion."""
    key = failure_key(recurrence)
    title = body = ''
    if created is None:
        title = f'Couldn\u2019t create the next \u201c{task.title}\u201d'
        body = (
            'The next occurrence of this repeating task was not created, usually because you have '
            'reached the open-task limit. Free some space by completing or deleting tasks, then '
            'uncomplete and complete this task again, or set the date manually.'
        )
    elif dropped_subtasks:
        title = f'The next \u201c{task.title}\u201d has no subtasks'
        body = (
            'The next occurrence of this repeating task was created, but its subtasks were not '
            'copied because you have reached the open-task limit. Add them yourself once you have '
            'freed some space.'
        )
    try:
        with transaction.atomic():
            notifications.delete_for_key(task.user, key, source='todo')
            if title:
                notifications.notify(
                    task.user,
                    source='todo',
                    title=title,
                    body=body,
                    url=reverse('todo:task', args=[task.number]),
                    key=key,
                )
    except Exception:
        logger.exception('Task %s: could not notify about its next occurrence', task.pk)


def _create_occurrence(
    task: Task, recurrence: Recurrence, due: date, task_service, *, with_subtasks: bool = True
) -> Task:
    user = task.user
    created = task_service.create_task(
        user,
        title=task.title,
        description=task.description,
        priority=task.priority,
        due_date=due,
        due_time=task.due_time,
        labels=list(task.labels.all()),
        recurrence=recurrence,
        source=TaskSource.RECURRENCE,
        allow_unpaid=True,
    )
    open_subtasks = (
        list(
            Task.objects.filter(parent=task, completed_at__isnull=True, deleted_at__isnull=True)
            .order_by('position', 'number')
            .prefetch_related('labels')
        )
        if with_subtasks
        else []
    )
    copies: list[Task] = []
    for subtask in open_subtasks:
        copies.append(
            task_service.create_task(
                user,
                title=subtask.title,
                description=subtask.description,
                priority=subtask.priority,
                labels=list(subtask.labels.all()),
                parent=created,
                source=TaskSource.RECURRENCE,
                allow_unpaid=True,
            )
        )
    if any(subtask.position > 0 for subtask in open_subtasks):  # keep a manual order
        for index, copy in enumerate(copies):
            Task.objects.filter(pk=copy.pk).update(position=index)
    copy_extras(task, created)
    return created
