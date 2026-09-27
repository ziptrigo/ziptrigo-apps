"""Runs `apps.core.scheduler` forever. Started in the `worker` container; never in `web`."""

import logging
import signal
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.scheduler import SchedulerRunner

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Run the in-process job scheduler (apps.core.scheduler) until terminated.'

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            '--tick-seconds',
            type=float,
            default=None,
            help='Override SCHEDULER_TICK_SECONDS for this run.',
        )

    def handle(self, *args: Any, **options: Any) -> None:
        tick_seconds = options['tick_seconds'] or settings.SCHEDULER_TICK_SECONDS
        runner = SchedulerRunner(tick_seconds=tick_seconds)

        def _stop(signum: int, _frame: Any) -> None:
            logger.info('run_scheduler received signal %s, stopping', signum)
            runner.stop()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        self.stdout.write(f'Scheduler running, tick={tick_seconds}s')
        runner.run_forever()
        self.stdout.write('Scheduler stopped')
