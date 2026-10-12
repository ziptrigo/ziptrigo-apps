"""Queueing `django.tasks` tasks from inside a transaction."""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def enqueue_after_commit(task, *args) -> None:
    """Queue `task` once the surrounding transaction (if there is one) has committed -- the task
    must see the rows it was queued for, and a rolled-back transaction must not queue anything.

    A failure to enqueue is logged and swallowed: by then the domain write is committed, so raising
    would only turn a committed change (a new password, a feedback message, an upload) into an
    error page the user would probably retry. Callers whose task matters more than a notification
    need their own safety net (receipts' `retry_due` job re-queues receipts nobody picked up; its
    daily job resumes frozen accounts)."""

    def _enqueue() -> None:
        try:
            task.enqueue(*args)
        except Exception:
            logger.exception('Could not queue %s%r', getattr(task, 'name', task), args)

    transaction.on_commit(_enqueue)
