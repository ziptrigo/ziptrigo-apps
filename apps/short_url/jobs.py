"""The jobs Short URLs registers with `apps.core.scheduler` (wired up in `apps.py`).

Both are idempotent (the scheduler is at-least-once) and let exceptions propagate: the runner
records them in `ScheduledJob.last_error`, which a swallowed one would show as a success. The
rollup in particular must stop at a day it can't write (see `services.rollup`).
"""

import logging

from .services import reputation, rollup

logger = logging.getLogger(__name__)


def rollup_clicks() -> None:
    """Hourly: write the daily totals of every complete UTC day that isn't rolled up yet."""
    days = rollup.rollup_clicks()
    if days:
        logger.info('Rolled up the clicks of %d day(s).', days)


def purge_click_events() -> None:
    """Daily: delete the raw events older than the retention (their daily totals stay)."""
    deleted = rollup.purge_click_events()
    if deleted:
        logger.info('Purged %d click event(s).', deleted)


def recheck_destinations() -> None:
    """Hourly: ask Safe Browsing again about the destinations of the active links that are due,
    and hold the ones that are now flagged. A no-op without `SAFE_BROWSING_API_KEY`; an API
    failure propagates (nothing is marked checked, so the next run takes the same links)."""
    result = reputation.recheck()
    if result.checked:
        logger.info(
            'Rechecked %d destination(s) with Safe Browsing; held %d link(s).',
            result.checked,
            result.held,
        )
