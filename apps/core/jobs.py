"""`core`'s own periodic job (issue #58): purging old `EmailVerification` rows. Registered from
`CoreConfig.ready()`, the same way each product registers its own jobs with `apps.core.scheduler`
-- see CLAUDE.md, "Queue and scheduler". `core` has no product to belong to, so it registers this
one on itself.
"""

import logging

from .services.email_verification import purge_old

logger = logging.getLogger(__name__)


def purge_old_email_verifications() -> None:
    """Daily: delete `EmailVerification` rows older than 30 days (`purge_old`'s default)."""
    try:
        deleted = purge_old()
    except Exception:
        logger.exception('purge_old_email_verifications failed')
        return
    if deleted:
        logger.info('purge_old_email_verifications deleted %d row(s)', deleted)
