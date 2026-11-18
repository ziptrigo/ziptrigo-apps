"""The jobs the QR code app registers with `apps.core.scheduler` (wired up in `apps.py`).

Idempotent (the scheduler is at-least-once); errors propagate, so the runner records them in
`ScheduledJob.last_error`.
"""

import logging

from apps.core.services.ip_retention import clear_old_ips

from .models import QRShareReport

logger = logging.getLogger(__name__)


def purge_report_ips() -> None:
    """Daily: forget the IP address of share-link reports older than 90 days."""
    cleared = clear_old_ips(QRShareReport.objects.all())
    if cleared:
        logger.info('Cleared the IP address of %d QR code report(s).', cleared)
