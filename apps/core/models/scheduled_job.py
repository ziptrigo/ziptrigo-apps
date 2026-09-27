from datetime import datetime
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class ScheduledJob(models.Model):
    """One row per job registered with `apps.core.scheduler`.

    A row is the job's lease: `run_scheduler`'s runner thread claims it with a single conditional
    `UPDATE` (see `apps.core.scheduler.runner.try_claim`) that only succeeds when the lease isn't
    currently held (`locked_until` is unset or in the past) and the job is due (`last_started_at`
    is unset or older than the job's interval). At-least-once, not exactly-once: a job's callable
    must be idempotent.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    name = cast(str, models.CharField(max_length=255, primary_key=True))
    interval_seconds = cast(int, models.PositiveIntegerField())
    locked_until = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    last_started_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    last_finished_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    last_success_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    last_error = models.TextField(blank=True, default='')
    run_count = cast(int, models.PositiveIntegerField(default=0))

    class Meta:
        verbose_name = 'Scheduled job'
        verbose_name_plural = 'Scheduled jobs'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name
