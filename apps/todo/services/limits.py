"""The limits of a task's and a label's fields, the first task number and the reminder limits
(defined in `apps.todo.limits`, which the models can import without the services)."""

from ..limits import (
    DESCRIPTION_MAX,
    FIRST_TASK_NUMBER,
    LABEL_DESCRIPTION_MAX,
    LABEL_NAME_MAX,
    MAX_REMINDER_MINUTES,
    MAX_REMINDERS_PER_TASK,
    MAX_SAVED_FILTERS,
    SAVED_FILTER_NAME_MAX,
    TITLE_MAX,
)

__all__ = [
    'DESCRIPTION_MAX',
    'FIRST_TASK_NUMBER',
    'LABEL_DESCRIPTION_MAX',
    'LABEL_NAME_MAX',
    'MAX_REMINDERS_PER_TASK',
    'MAX_REMINDER_MINUTES',
    'MAX_SAVED_FILTERS',
    'SAVED_FILTER_NAME_MAX',
    'TITLE_MAX',
]
