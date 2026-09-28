from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

#: Bounds for the email verification knobs below (issue #58 follow-up): wide enough to allow real
#: retuning from the admin, narrow enough that no admin-set value can break the service outright
#: (a 0-digit code always matches, 0 attempts burns every row on arrival, 0 minutes' validity
#: expires a link before it can ever be used).
_CODE_LENGTH_MIN, _CODE_LENGTH_MAX = 6, 10
_MAX_ATTEMPTS_MIN, _MAX_ATTEMPTS_MAX = 1, 10
_VALIDITY_MINUTES_MIN = 1


class CoreSettings(models.Model):
    """Singleton admin-editable settings for cross-app `core` functionality (issue #58): today
    just the knobs `apps.core.services.email_verification` reads on every call. Always the row
    with `pk=1`; use `CoreSettings.load()`. Mirrors `apps.file_transfer.models.FileTransferSettings`.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    # Email verification (issue #58 initial values: 6 digits, 30 minutes, 5 attempts, 60s cooldown).
    email_verification_code_length = cast(
        int,
        models.PositiveSmallIntegerField(
            default=6,
            verbose_name='Verification code length',
            help_text='Number of digits in a generated email verification code.',
            validators=[
                MinValueValidator(_CODE_LENGTH_MIN),
                MaxValueValidator(_CODE_LENGTH_MAX),
            ],
        ),
    )
    email_verification_validity_minutes = cast(
        int,
        models.PositiveIntegerField(
            default=30,
            verbose_name='Verification validity (minutes)',
            help_text='How long an email verification code/link stays valid after it is sent.',
            validators=[MinValueValidator(_VALIDITY_MINUTES_MIN)],
        ),
    )
    email_verification_max_attempts = cast(
        int,
        models.PositiveSmallIntegerField(
            default=5,
            verbose_name='Verification max attempts',
            help_text=(
                'Wrong codes allowed before a verification is burned and a resend is required.'
            ),
            validators=[
                MinValueValidator(_MAX_ATTEMPTS_MIN),
                MaxValueValidator(_MAX_ATTEMPTS_MAX),
            ],
        ),
    )
    email_verification_resend_cooldown_seconds = cast(
        int,
        models.PositiveIntegerField(
            default=60,
            verbose_name='Verification resend cooldown (seconds)',
            help_text='Minimum time between two verification sends for the same email+purpose.',
            validators=[MinValueValidator(0)],
        ),
    )

    class Meta:
        verbose_name = 'Core settings'
        verbose_name_plural = 'Core settings'

    def __str__(self) -> str:
        return 'Core settings'

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'CoreSettings':
        """Return the singleton row, creating it with defaults on first use."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
