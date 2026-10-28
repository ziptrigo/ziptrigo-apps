"""What happens to a user's stored files when the user goes away (issue #184).

`delete_user_storage` is queued by the `pre_delete(User)` hook in `apps.todo.apps`: deleting a user
cascades their rows, and every object of theirs sits under one prefix
(`services.storage.user_prefix`), so one `delete_prefix` removes them all.
"""

import logging
from datetime import timedelta
from uuid import UUID

from django.tasks import task
from django.utils import timezone

from .storage import get_storage, user_prefix

logger = logging.getLogger(__name__)

#: How long to wait before each retry of `delete_user_storage` (so it runs at most one more time
#: than there are delays). The `User` row is gone by then, so nothing else records that the objects
#: are still there: the task itself has to keep trying.
USER_STORAGE_RETRY_DELAYS = (
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)


@task(queue_name='default')
def delete_user_storage(user_id: str, attempt: int = 1) -> None:
    """Delete every object stored for a (deleted) user, in one `delete_prefix`.

    `delete_prefix` raises when S3 refuses any object, and the task queue never retries a failed
    task, so a failure queues this task again (`attempt` + 1) after the next of
    `USER_STORAGE_RETRY_DELAYS`: bounded, and durable because the retry is a queued task. Only
    after the last try does it give up, logging an error with the prefix -- the objects then stay
    in the bucket (and need removing by hand)."""
    prefix = user_prefix(UUID(user_id))
    try:
        deleted = get_storage().delete_prefix(prefix)
    except Exception:
        if attempt > len(USER_STORAGE_RETRY_DELAYS):
            logger.exception(
                'Giving up deleting the to-do objects under %s after %s attempts: '
                'they remain in the bucket',
                prefix,
                attempt,
            )
            return
        delay = USER_STORAGE_RETRY_DELAYS[attempt - 1]
        logger.warning(
            'Deleting the to-do objects under %s failed (attempt %s); retrying in %s',
            prefix,
            attempt,
            delay,
            exc_info=True,
        )
        _requeue_user_storage(user_id, attempt + 1, delay)
        return
    logger.info('Deleted %s to-do objects under %s', deleted, prefix)


def _requeue_user_storage(user_id: str, attempt: int, delay: timedelta) -> None:
    """Queue `delete_user_storage` again to run after `delay`. A backend that cannot defer a task
    (the immediate one the tests run on) runs it at once."""
    retry = delete_user_storage
    if retry.get_backend().supports_defer:
        retry = retry.using(run_after=timezone.now() + delay)
    retry.enqueue(user_id, attempt)
