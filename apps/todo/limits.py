"""The limits of a task's and a label's fields, and the first task number.

They live here, not in `services/limits.py`, because the models need them and a model can't import
from the `services` package (its `__init__` imports the models). `services.limits` re-exports them.
"""

TITLE_MAX = 500
DESCRIPTION_MAX = 20_000
LABEL_NAME_MAX = 50
LABEL_DESCRIPTION_MAX = 255
#: Task numbers are per user and start here, so `#1` to `#999` are never task references in
#: Markdown (`apps.core.markdown` only links numbers of 4 to 9 figures).
FIRST_TASK_NUMBER = 1000

#: A reminder is set this many minutes before the due time, at most 30 days.
MAX_REMINDER_MINUTES = 43_200
#: At most this many reminders on one task.
MAX_REMINDERS_PER_TASK = 10
