"""The reminder endpoints (`/api/todo/tasks/{number}/reminders`, `/api/todo/reminders/history`):
add, change and remove a task's reminders, and the history of what was delivered. Each calls the
service the task page uses; adding, changing and removing spend `TODO_EDIT_USER`.

A reminder is addressed by its id under its task's number (a reminder of another task is a 404, as
is another user's task). The task must be open and have a due date for a reminder to be added or
changed (400 otherwise).
"""

from uuid import UUID

from django.shortcuts import get_object_or_404

from .. import services
from ..models import Reminder, ReminderDelivery, Task
from ..schemas import (
    DeliveryListSchema,
    ReminderCreateSchema,
    ReminderSchema,
    ReminderUpdateSchema,
)
from .common import DEFAULT_LIMIT, charge, clamp_page, mapped_errors, owned_task, reminder_data
from .router import router


def _owned_reminder(task: Task, reminder_id: UUID) -> Reminder:
    """The reminder, if it is one of `task`'s; any other is a 404."""
    return get_object_or_404(
        Reminder.objects.select_related('task', 'task__user'), pk=reminder_id, task=task
    )


@router.post('/tasks/{number}/reminders', response={201: ReminderSchema})
def add_reminder(request, number: int, payload: ReminderCreateSchema):
    """Add a reminder `minutes_before` (0 to 43,200: 30 days) the task's due time, delivered
    `in_app`, by `email` or `both` (default: the channel of your default reminder). A date-only task
    is due at your default due time. One reminder per time, at most 10 per task."""
    task = owned_task(request, number)
    charge(request, 'TODO_EDIT_USER')
    channel = payload.channel or services.get_account(request.auth).default_reminder_channel
    with mapped_errors():
        reminder = services.reminders.add_reminder(
            task, minutes_before=payload.minutes_before, channel=channel
        )
    return 201, reminder_data(reminder)


@router.patch('/tasks/{number}/reminders/{reminder_id}', response=ReminderSchema)
def update_reminder(request, number: int, reminder_id: UUID, payload: ReminderUpdateSchema):
    """Change a reminder's time and/or channel (a field left out, or `null`, stays). A new time
    arms it again."""
    task = owned_task(request, number)
    reminder = _owned_reminder(task, reminder_id)
    charge(request, 'TODO_EDIT_USER')
    with mapped_errors():
        services.reminders.update_reminder(
            reminder, minutes_before=payload.minutes_before, channel=payload.channel
        )
    return reminder_data(reminder)


@router.delete('/tasks/{number}/reminders/{reminder_id}', response={204: None})
def delete_reminder(request, number: int, reminder_id: UUID):
    """Remove a reminder (what it already delivered stays in the history)."""
    task = owned_task(request, number)
    reminder = _owned_reminder(task, reminder_id)
    charge(request, 'TODO_EDIT_USER')
    with mapped_errors():
        services.reminders.delete_reminder(reminder)
    return 204, None


@router.get('/reminders/history', response=DeliveryListSchema)
def reminder_history(request, limit: int = DEFAULT_LIMIT, offset: int = 0):
    """Every reminder delivered to you, newest first. `limit` (1-100, default 50) and `offset`
    page the result; `count` is the total."""
    limit, offset = clamp_page(limit, offset)
    queryset = ReminderDelivery.objects.filter(user=request.auth).order_by('-sent_at', 'pk')
    rows = list(queryset[offset : offset + limit])
    return {
        'count': queryset.count(),
        'limit': limit,
        'offset': offset,
        'results': [
            {
                'id': row.pk,
                'task': row.task_number if row.task_id is not None else None,
                'task_number': row.task_number,
                'task_title': row.task_title,
                'channel': row.channel,
                'note': row.note,
                'fire_at': row.fire_at,
                'sent_at': row.sent_at,
            }
            for row in rows
        ],
    }
