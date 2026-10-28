from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.singleton import load_singleton


class ShortUrlSettings(models.Model):
    """Singleton admin-editable settings for Short URLs (issue #162): the one-off prices of the
    extras, the per-user link allowances and the analytics and abuse knobs. Always the row with
    `pk=1`; use `ShortUrlSettings.load()`.

    Creating and following a link is free. Each extra costs a one-off number of credits *per
    link*, and so does every active link beyond `free_active_links`. A price of 0 makes that
    extra free. There is no recurring charge.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: int

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    custom_alias_price = cast(
        int,
        models.PositiveIntegerField(
            default=1,
            verbose_name='Custom alias price (credits)',
            help_text='One-off, per link, when it is created with an alias of its own.',
        ),
    )
    password_price = cast(
        int,
        models.PositiveIntegerField(
            default=1,
            verbose_name='Password price (credits)',
            help_text='One-off, per link, the first time a password is set on it.',
        ),
    )
    analytics_price = cast(
        int,
        models.PositiveIntegerField(
            default=1,
            verbose_name='Detailed analytics price (credits)',
            help_text='One-off, per link, to see its detailed statistics.',
        ),
    )
    extra_link_price = cast(
        int,
        models.PositiveIntegerField(
            default=1,
            verbose_name='Extra link price (credits)',
            help_text='One-off, per link, when it becomes active beyond the free allowance.',
        ),
    )
    free_active_links = cast(
        int,
        models.PositiveIntegerField(
            default=25,
            verbose_name='Free active links (per user)',
            help_text='Active links a user may have without paying the extra link price.',
        ),
    )
    max_active_links = cast(
        int,
        models.PositiveIntegerField(
            default=500,
            validators=[MinValueValidator(1)],
            verbose_name='Max links (per user)',
            help_text='A hard cap on non-deleted links (active and disabled): no more can be '
            'created beyond it. Deleting a link makes room.',
        ),
    )
    click_event_retention_days = cast(
        int,
        models.PositiveIntegerField(
            default=90,
            validators=[MinValueValidator(1)],
            help_text='Days a raw click event is kept before only its daily totals remain.',
        ),
    )
    auto_hold_report_threshold = cast(
        int,
        models.PositiveIntegerField(
            default=0,
            help_text='Hold a link for review once it has this many pending reports from '
            'different addresses. 0 turns it off.',
        ),
    )

    class Meta:
        verbose_name = 'Short URL settings'
        verbose_name_plural = 'Short URL settings'

    def __str__(self) -> str:
        return 'Short URL settings'

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'ShortUrlSettings':
        """Return the singleton row, creating it with defaults on first use."""
        return load_singleton(cls)

    def clean(self) -> None:
        if self.free_active_links > self.max_active_links:
            raise ValidationError(
                {
                    'free_active_links': 'The free allowance cannot be higher than the cap '
                    'on active links.'
                }
            )
