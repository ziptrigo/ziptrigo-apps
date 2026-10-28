from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.singleton import load_singleton


class TodoSettings(models.Model):
    """Singleton admin-editable settings for the To-do app (issue #178): the task allowance and
    price, and the caps. Always the row with `pk=1`; use `TodoSettings.load()`.

    Later sub-issues add their own fields (with their own migrations).
    The storage price per GB per day is not here: it is the shared `BillingSettings` one.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: int

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    free_tasks_per_month = cast(
        int,
        models.PositiveIntegerField(
            default=10,
            help_text='Tasks a user may create each calendar month before the price applies. '
            'Subtasks and every occurrence of a repeating task count.',
        ),
    )
    task_price = cast(
        int,
        models.PositiveIntegerField(
            default=10,
            verbose_name='Task price (credits)',
            help_text='Credits per task beyond the free ones. 0 makes tasks free.',
        ),
    )
    trash_days = cast(
        int,
        models.PositiveIntegerField(
            default=30,
            validators=[MinValueValidator(1)],
            help_text='Days a trashed task is kept before it is deleted for good.',
        ),
    )
    max_open_tasks_per_user = cast(
        int,
        models.PositiveIntegerField(
            default=10_000,
            validators=[MinValueValidator(1)],
            help_text='A hard cap on non-trashed open tasks per user: no more can be created '
            'beyond it.',
        ),
    )
    max_labels_per_user = cast(
        int,
        models.PositiveIntegerField(
            default=200,
            validators=[MinValueValidator(1)],
            help_text='Labels a user may have.',
        ),
    )

    max_attachment_bytes = cast(
        int,
        models.PositiveBigIntegerField(
            default=50 * 1024 * 1024,
            validators=[MinValueValidator(1)],
            help_text='The largest file that can be attached to a task, in bytes (default 50 MiB).',
        ),
    )
    max_attachments_per_task = cast(
        int,
        models.PositiveIntegerField(
            default=20,
            validators=[MinValueValidator(1)],
            help_text='Files that can be attached to one task (uploads in progress count).',
        ),
    )

    class Meta:
        verbose_name = 'To-do settings'
        verbose_name_plural = 'To-do settings'

    def __str__(self) -> str:
        return 'To-do settings'

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'TodoSettings':
        """Return the singleton row, creating it with defaults on first use."""
        return load_singleton(cls)
