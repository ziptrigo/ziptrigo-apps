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

**Month lengths.** `BYMONTHDAY` beyond a month's length is skipped by `dateutil`, not clamped:
`BYMONTHDAY=31` has no occurrence in February, April, June, September or November. `-1` is the last
day of the month, whatever its length. A monthly or yearly rule with no day of its own repeats on
the day of the date it is computed from, so it follows the same skipping (from 31 Jan, the next is
31 Mar).

**COUNT** is not left to `dateutil`: a count would restart at every computed date (each call starts
a new series at `after`). `Recurrence.occurrences_created` counts the occurrences made, and
`next_date` ignores `COUNT`.
"""

import logging
import re
from datetime import UTC, date, datetime, time, timedelta

from dateutil.rrule import rrulestr
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services.timezones import user_today

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


def parse_rule(value: str) -> str:
    """Validate `value` (an `RRULE` property value, no `DTSTART`) and return its canonical form.

    Raises `ValidationError('Unsupported repeat rule.')` for anything outside the subset in the
    module docstring, and a different message for a rule that is too long or never occurs again.
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
        today = datetime.now(UTC).date()
        first = _iterate(result, today - timedelta(days=1), keep_count=True)
    except ValueError, TypeError, OverflowError, IndexError:
        raise _unsupported() from None
    if first is None:
        raise ValidationError(NO_UPCOMING)
    return result


def _iterate(
    rrule: str, after: date, *, keep_count: bool = False, inclusive: bool = False
) -> date | None:
    """The first date strictly after `after` (`inclusive`: on or after) that `rrule` yields, for
    the series that starts at `after`."""
    if not keep_count:
        rrule = ';'.join(part for part in rrule.split(';') if not part.startswith('COUNT='))
    start_at = datetime.combine(after, time())
    found = rrulestr('RRULE:' + rrule, dtstart=start_at).after(start_at, inc=inclusive)
    return found.date() if found is not None else None


def next_date(rrule: str, after: date) -> date | None:
    """The first date after `after` the rule yields, or `None` when it is exhausted (`UNTIL`).

    `COUNT` is ignored here: it is tracked by `Recurrence.occurrences_created` (see the module
    docstring). A `BYMONTHDAY` beyond the month's length skips that month; `-1` is its last day.
    """
    return _iterate(rrule, after)


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


def clean_rule(value: object) -> str:
    """`parse_rule`, with its refusal keyed to the `rrule` field."""
    try:
        return parse_rule(str(value or ''))
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
    return Recurrence.objects.create(user=user, rrule=rrule, mode=mode, anchor_date=anchor)


def change(task: Task, rrule: str, mode: str, anchor: date) -> Recurrence:
    """Give `task` this rule: the running `Recurrence` is edited in place (under its row lock, so a
    completion at the same moment sees either the old rule or the new one), else a new one is made
    (also when the task's earlier one had ended). Editing the schedule sets the anchor to the
    task's date. Call it inside the caller's transaction."""
    active = task.active_recurrence
    if active is None:
        return start(task.user, rrule, mode, anchor)
    locked = Recurrence.objects.select_for_update().get(pk=active.pk)
    locked.rrule, locked.mode, locked.anchor_date = rrule, mode, anchor
    locked.save(update_fields=['rrule', 'mode', 'anchor_date'])
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

    The `Recurrence` row is locked first, and the anchor it holds must still be the one `task`
    was loaded with: a mismatch means another call already advanced the rule, so nothing is created
    (two tabs completing at once make one next occurrence). The next date is counted from the
    anchor (fixed) or from the user's today (from completion). It is created with
    `allow_unpaid=True`: completing a repeating task always succeeds, and an occurrence the balance
    can't cover is owed (`services.billing`). Open subtasks are copied the same way. Returns the
    new occurrence, if one was made.
    """
    from . import tasks as task_service

    if task.recurrence_id is None or task.recurrence is None:
        return None
    seen_anchor = task.recurrence.anchor_date  # as this task was loaded; never refreshed below
    user = task.user
    with transaction.atomic():
        recurrence = Recurrence.objects.select_for_update().filter(pk=task.recurrence_id).first()
        if recurrence is None or recurrence.ended_at is not None:
            return None
        if recurrence.anchor_date != seen_anchor:
            return None
        count = count_of(recurrence.rrule)
        base = (
            recurrence.anchor_date if recurrence.mode == RecurrenceMode.FIXED else user_today(user)
        )
        nxt = next_date(recurrence.rrule, base)
        if nxt is None or (count is not None and recurrence.occurrences_created >= count):
            ended_at = timezone.now()
            Recurrence.objects.filter(pk=recurrence.pk).update(ended_at=ended_at)
            task.recurrence.ended_at = ended_at  # the caller's copy reflects it
            return None
        try:
            with transaction.atomic():  # a refusal (the open-task cap) undoes the partial copy
                created = _create_occurrence(task, recurrence, nxt, task_service)
        except ValidationError:
            logger.warning('Task %s: the next occurrence could not be created', task.pk)
            return None
        recurrence.anchor_date = nxt
        recurrence.occurrences_created += 1
        recurrence.save(update_fields=['anchor_date', 'occurrences_created'])
    return created


def _create_occurrence(task: Task, recurrence: Recurrence, due: date, task_service) -> Task:
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
    open_subtasks = list(
        Task.objects.filter(parent=task, completed_at__isnull=True, deleted_at__isnull=True)
        .order_by('position', 'number')
        .prefetch_related('labels')
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
