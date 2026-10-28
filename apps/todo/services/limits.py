"""The limits of a task's and a label's fields, and the first task number (defined in
`apps.todo.limits`, which the models can import without the services)."""

from ..limits import (
    DESCRIPTION_MAX,
    FIRST_TASK_NUMBER,
    LABEL_DESCRIPTION_MAX,
    LABEL_NAME_MAX,
    TITLE_MAX,
)

__all__ = [
    'DESCRIPTION_MAX',
    'FIRST_TASK_NUMBER',
    'LABEL_DESCRIPTION_MAX',
    'LABEL_NAME_MAX',
    'TITLE_MAX',
]
