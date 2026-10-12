"""Queueing tasks from inside a transaction."""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def enqueue_after_commit(task, *args) -> None:
    """Queue `task` once the surrounding transaction (if there is one) has committed -- the task
    must see the rows it was queued for, and a rolled-back transaction must not queue anything.

    A failure to enqueue is logged and swallowed: by then the domain write is committed, and every
    task queued this way has a safety net (the `retry_due` job re-queues receipts nobody picked up;
    the daily job resumes frozen accounts), so raising would only turn a committed upload or top-up
    into an error page."""

    def _enqueue() -> None:
        try:
            task.enqueue(*args)
        except Exception:
            logger.exception('Could not queue %s%r', getattr(task, 'name', task), args)

    transaction.on_commit(_enqueue)
