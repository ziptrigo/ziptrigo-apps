from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.singleton import load_singleton


class WorldTimeSettings(models.Model):
    """Singleton admin-editable settings for the World Time app (issue #269): the caps. The app is
    free, so there are no prices. Always the row with `pk=1`; use `WorldTimeSettings.load()`."""

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: int

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    max_tabs_per_user = cast(
        int,
        models.PositiveIntegerField(
            default=10,
            validators=[MinValueValidator(1)],
            help_text='Tabs (named sets of locations) a user may have.',
        ),
    )
    max_locations_per_tab = cast(
        int,
        models.PositiveIntegerField(
            default=20,
            validators=[MinValueValidator(1)],
            help_text='Locations one tab may hold.',
        ),
    )

    max_events_per_user = cast(
        int,
        models.PositiveIntegerField(
            default=50,
            db_default=50,
            validators=[MinValueValidator(1)],
            help_text='Shared events a user may have at a time (ended ones count until they '
            'are deleted).',
        ),
    )
    event_purge_days = cast(
        int,
        models.PositiveIntegerField(
            default=30,
            db_default=30,
            validators=[MinValueValidator(1)],
            help_text='Days after a shared event ends before it is deleted and its link stops '
            'working.',
        ),
    )

    class Meta:
        verbose_name = 'World Time settings'
        verbose_name_plural = 'World Time settings'

    def __str__(self) -> str:
        return 'World Time settings'

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'WorldTimeSettings':
        """Return the singleton row, creating it with defaults on first use."""
        return load_singleton(cls)
