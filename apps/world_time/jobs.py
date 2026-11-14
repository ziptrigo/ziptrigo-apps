"""The jobs the World Time app registers with `apps.core.scheduler` (wired up in `apps.py`).

Idempotent (the scheduler is at-least-once); an error propagates so the runner records it in
`ScheduledJob.last_error`.
"""

import logging

from .services import events

logger = logging.getLogger(__name__)


def purge_events() -> None:
    """Daily: delete the shared events that ended longer than `event_purge_days` ago."""
    deleted = events.purge_ended()
    if deleted:
        logger.info('Purged %d ended World Time event(s).', deleted)
