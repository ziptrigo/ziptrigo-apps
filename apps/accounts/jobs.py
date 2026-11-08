"""`accounts`' periodic job: purging expired `RefreshToken` rows (issue #252). It lets its
exceptions propagate: the scheduler's runner records them in `ScheduledJob.last_error`, which a
swallowed one would hide. Registered from `AccountsConfig.ready()`."""

import logging

from django.utils import timezone

from .models import RefreshToken

logger = logging.getLogger(__name__)

#: Rows deleted per statement: short transactions on a table that grows with every refresh.
PURGE_BATCH = 1000


def purge_refresh_tokens() -> None:
    """Daily: delete `RefreshToken` rows whose token has expired, in batches. A token past its
    `exp` is refused by its signature check anyway, so the row (rotated or revoked or not) is no
    longer needed. Idempotent and safe to run twice at once."""
    cutoff = timezone.now()
    total = 0
    while True:
        ids = list(
            RefreshToken.objects.filter(expires_at__lt=cutoff).values_list('pk', flat=True)[
                :PURGE_BATCH
            ]
        )
        if not ids:
            break
        total += RefreshToken.objects.filter(pk__in=ids).delete()[0]
    if total:
        logger.info('purge_refresh_tokens deleted %d row(s)', total)
