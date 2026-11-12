"""The two low-level pieces `services.tasks` and `services.blockers` share (issues #272, #273):
locking tasks, and writing an open task's state.

Kept apart because `tasks` closes tasks and so calls `blockers`, while `blockers` changes states
and so needs the same state write `tasks.set_state` does.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from apps.accounts.services.timezones import user_today

from ..models import MUTED_STATES, Task
from . import reminders
from .dates import next_today_position


def lock_tasks(*pks: UUID) -> dict[UUID, Task]:
    """The tasks `pks`, re-read with their rows locked (in pk order, so two callers locking the same
    rows can't deadlock) and their owner loaded. Call it inside a transaction; a task that is gone
    is missing from the result."""
    rows: Iterable[Task] = (
        Task.objects.select_related('user')
        .select_for_update(of=('self',))
        .filter(pk__in=pks)
        .order_by('pk')
    )
    return {task.pk: task for task in rows}


def write_state(current: Task, state: str, now: datetime, *, release: bool = True) -> bool:
    """Set `state` on `current`, an **open** task read under its row lock with its owner loaded
    (`lock_tasks`); a no-op for the state it already has. Returns whether it changed.

    A task due today that changes state goes to the end of Today's manual order
    (`next_today_position`, which is 0 while Today has none). Leaving a muted state delivers the
    reminders it held (`reminders.release_held`: one reminder, on the channels they used) unless
    `release` is false -- a trashed task's reminders are left to the job. Call it in the
    transaction that decided the change.
    """
    if current.state == state:
        return False
    was_muted = current.state in MUTED_STATES
    fields: dict[str, Any] = {'state': state, 'updated_at': now}
    if current.due_date is not None and current.due_date == user_today(current.user):
        fields['today_position'] = next_today_position(current.user, current.due_date)
    Task.objects.filter(pk=current.pk).update(**fields)
    for name, value in fields.items():
        setattr(current, name, value)
    if release and was_muted and state not in MUTED_STATES:
        reminders.release_held(current, now)
    return True
