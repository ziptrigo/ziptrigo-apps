from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.singleton import load_singleton


class NotesSettings(models.Model):
    """Singleton admin-editable settings for the Notes app (issue #260): the free slots, the
    prices and the caps. Always the row with `pk=1`; use `NotesSettings.load()`.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: int

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    free_notes = cast(
        int,
        models.PositiveIntegerField(
            default=5,
            help_text='Notes a user may have outside the Trash before the price applies. A note '
            'created in a free slot is free for as long as it lives, renewals included. '
            'Trashing it frees the slot for the next note.',
        ),
    )
    creation_price = cast(
        int,
        models.PositiveIntegerField(
            default=1,
            verbose_name='Creation price (credits)',
            help_text='Credits to create a note beyond the free slots. 0 makes it free to create.',
        ),
    )
    renewal_price = cast(
        int,
        models.PositiveIntegerField(
            default=5,
            verbose_name='Renewal price (credits)',
            help_text='Credits charged every renewal period for a note that is not free. '
            '0 makes renewals free.',
        ),
    )
    renewal_days = cast(
        int,
        models.PositiveIntegerField(
            default=365,
            validators=[MinValueValidator(1)],
            help_text='Days a paid note is paid for: it renews this many days after it was '
            'created or last renewed.',
        ),
    )
    renewal_warning_days = cast(
        int,
        models.PositiveIntegerField(
            default=7,
            validators=[MinValueValidator(1)],
            help_text='How many days ahead the owner is warned when their balance will not '
            'cover the notes about to renew.',
        ),
    )
    trash_days = cast(
        int,
        models.PositiveIntegerField(
            default=30,
            validators=[MinValueValidator(1)],
            help_text='Days a trashed note is kept before it is deleted for good.',
        ),
    )
    max_notes_per_user = cast(
        int,
        models.PositiveIntegerField(
            default=2000,
            validators=[MinValueValidator(1)],
            help_text='A hard cap on notes outside the Trash per user: no more can be created '
            'beyond it.',
        ),
    )
    max_tags_per_user = cast(
        int,
        models.PositiveIntegerField(
            default=200,
            validators=[MinValueValidator(1)],
            help_text='Tags a user may have.',
        ),
    )
    max_links_per_note = cast(
        int,
        models.PositiveIntegerField(
            default=10,
            validators=[MinValueValidator(1)],
            help_text='Active share links one note may have at a time.',
        ),
    )
    max_versions_per_note = cast(
        int,
        models.PositiveIntegerField(
            default=50,
            validators=[MinValueValidator(1)],
            help_text='Versions kept per note; saving beyond it deletes the oldest.',
        ),
    )

    class Meta:
        verbose_name = 'Notes settings'
        verbose_name_plural = 'Notes settings'

    def __str__(self) -> str:
        return 'Notes settings'

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'NotesSettings':
        """Return the singleton row, creating it with defaults on first use."""
        return load_singleton(cls)
