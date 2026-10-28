"""The To-do JWT API (`/api/todo/`, issue #187): tasks, labels, comments, reminders, attachments,
saved filters, settings, the email inbox and the AI date reader -- logged-in users only, calling the
same `apps.todo.services` the web pages do. See `router.py` for why the endpoint modules share one
`Router` instance, and `common.py` for the status codes.
"""

from . import (
    attachments as _attachments,  # noqa: F401 -- imported for its side effect (registers endpoints)
)
from . import comments as _comments  # noqa: F401 -- same
from . import filters as _filters  # noqa: F401 -- same
from . import labels as _labels  # noqa: F401 -- same
from . import parse as _parse  # noqa: F401 -- same
from . import reminders as _reminders  # noqa: F401 -- same
from . import settings as _settings  # noqa: F401 -- same
from . import tasks as _tasks  # noqa: F401 -- same
from .router import router

__all__ = ['router']
