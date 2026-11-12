"""The activity log of a task (issue #274): what is recorded, and how.

Every change to a task is written here as a `TaskEvent`, by the **services** (never the views), so a
change made from the web, the API, an email, a phone notification or a job is recorded alike. The
log asks for **no row lock of its own** (no `select_for_update`) and its foreign keys are declared
without a database constraint (`TaskEvent.task` and `.user`: `db_constraint=False`), so an insert
takes no `FOR KEY SHARE` on the task or user row either, which on PostgreSQL would conflict with the
`FOR UPDATE` the services take and could close a deadlock cycle. An entry is one insert inside the
transaction of the change it records (`lock_account` first, then the tasks, as `services.blockers`
describes), so it commits or rolls back with it and can't change the lock order.

**Never fails the change.** The insert runs in its own savepoint and a failure is logged, not raised:
a user's edit must not be refused because its history line could not be written.

**Only real changes are recorded** (callers compare the old and new values), one entry per logical
field: a due date and its time are one entry, labels are one entry listing what was added and
removed, an edit that changes several fields writes several entries.

**The channel** says which entry point made the change (`EventChannel`). It is not passed down
through the services: a context variable of `core` (`apps.core.request_channel`) holds it, `system`
by default (jobs, the shell). Its `RequestChannelMiddleware` sets `api` for `/api/` requests and
`web` for every other; the phone's answer handler, the email intake and anything else that is not a request
wrap their work in `with channel(EventChannel.PUSH)` (or `EMAIL`).

**`automatic`** marks a change nobody asked for in that moment: an automatic Blocked or unblock, the
next occurrence of a repeating task, a task made from an email, a repeat rule that ended, and what a
parent's close or trash does to its subtasks. The phone's Complete button is the user's action, so
it is not automatic.

The `data` of each kind (every value is a snapshot, so an entry reads right after what it names is
gone; `services.event_text` writes the sentence):

- `created`: `source`, `parent` (`{number, title}`), `previous` (`{number}`: the occurrence it follows),
  `rrule`.
- `state`: `from`, `to` (state values). `closed`: `resolution`, `via` (`{number, title}` of the parent that
  closed it), `subtasks` (how many open subtasks the same close closed) and `subtasks_resolution`.
  `reopened`: `to` (To do or Blocked). `resolution`: `from`, `to`.
- `title`, `priority`: `from`, `to`. `due`: `from_date`, `from_time`, `to_date`, `to_time` (ISO).
  `labels`: `added`, `removed` (names). `repeat`: `from`, `to` (rules, `''` for none), `mode`,
  `reason` (`ended`). `description`: nothing. `checklist`: `checked`, `text`.
- `blocker_added`, `blocker_removed`: `number`, `title`. `blocked`: `blockers` (`[{number, title}]`).
  `unblocked`: `blockers` (`[{number, title, reason}]`, reason `closed`, `trashed`, `deleted`,
  `removed`).
- `subtask_added`, `subtask_removed`: `number`, `title`, `moved`. `subtask_closed`: `number`,
  `title`, `resolution`. `moved`: `parent` (`{number, title}` or none: made top level).
- `attachment_added`, `attachment_removed`: `filename`, `comment` (a comment's file).
- `trashed`, `restored`: `via` (the parent that did it to its subtask).
"""

import logging
from collections.abc import Iterable
from datetime import date, time, timedelta
from typing import Any, NamedTuple
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.core.request_channel import channel, current_channel

from ..models import EventKind, Task, TaskEvent

logger = logging.getLogger(__name__)

__all__ = ['channel', 'current_channel']  # re-exported: the services say `events.channel(...)`


class Entry(NamedTuple):
    """One entry to write: the task it is about (`task_id`, and its owner `user_id`), the kind, the
    data and whether it was automatic."""

    task_id: UUID
    user_id: UUID
    kind: str
    data: dict[str, Any]
    automatic: bool = False


def entry(
    task: Task, kind: str, data: dict[str, Any] | None = None, *, automatic: bool = False
) -> Entry:
    return Entry(task.pk, task.user_id, kind, data or {}, automatic)


def record_many(entries: Iterable[Entry]) -> None:
    """Write the entries (one insert) in the caller's transaction, in a savepoint of their own;
    never raises (see the module docstring). Their times are one microsecond apart, in order, so
    "newest first" keeps the order they were written in."""
    rows = list(entries)
    if not rows:
        return
    now = timezone.now()
    where = current_channel()
    try:
        with transaction.atomic():
            TaskEvent.objects.bulk_create(
                [
                    TaskEvent(
                        task_id=row.task_id,
                        user_id=row.user_id,
                        kind=row.kind,
                        data=row.data,
                        automatic=row.automatic,
                        channel=where,
                        created_at=now + timedelta(microseconds=index),
                    )
                    for index, row in enumerate(rows)
                ]
            )
    except Exception:
        logger.exception('Could not record %d task event(s) (first: %s)', len(rows), rows[0].kind)


def record(
    task: Task, kind: str, data: dict[str, Any] | None = None, *, automatic: bool = False
) -> None:
    """Write one entry about `task`."""
    record_many([entry(task, kind, data, automatic=automatic)])


# -- Small helpers the services share ------------------------------------------------------------


def task_ref(task: Task) -> dict[str, Any]:
    """`{number, title}` of a task: how an entry names another task."""
    return {'number': task.number, 'title': task.title}


def iso(value: date | time | None) -> str | None:
    return value.isoformat() if value is not None else None


def due_data(
    old_date: date | None, old_time: time | None, new_date: date | None, new_time: time | None
) -> dict[str, Any]:
    return {
        'from_date': iso(old_date),
        'from_time': iso(old_time),
        'to_date': iso(new_date),
        'to_time': iso(new_time),
    }


def due_entry(
    task: Task,
    old_date: date | None,
    old_time: time | None,
    new_date: date | None,
    new_time: time | None,
) -> Entry | None:
    """The `due` entry for a change of date and/or time, or `None` when neither changed."""
    if old_date == new_date and old_time == new_time:
        return None
    return entry(task, EventKind.DUE, due_data(old_date, old_time, new_date, new_time))
