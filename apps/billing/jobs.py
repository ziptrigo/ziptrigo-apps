"""`billing`'s periodic jobs, registered from `BillingConfig.ready()` (see CLAUDE.md, "Queue and
scheduler"). Exceptions propagate: the runner records them in `ScheduledJob.last_error`."""

import logging

from .services.promo import release_stale_reservations

logger = logging.getLogger(__name__)


def release_promo_reservations() -> None:
    """Daily: give back the uses of promo codes reserved at signup by accounts that never
    confirmed their email (`RESERVATION_MAX_AGE`, 30 days). Idempotent."""
    released = release_stale_reservations()
    if released:
        logger.info('release_promo_reservations released %d reservation(s)', released)
