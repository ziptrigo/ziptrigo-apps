from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models


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
        ),
    )
    email_verification_validity_minutes = cast(
        int,
        models.PositiveIntegerField(
            default=30,
            verbose_name='Verification validity (minutes)',
            help_text='How long an email verification code/link stays valid after it is sent.',
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
        ),
    )
    email_verification_resend_cooldown_seconds = cast(
        int,
        models.PositiveIntegerField(
            default=60,
            verbose_name='Verification resend cooldown (seconds)',
            help_text='Minimum time between two verification sends for the same email+purpose.',
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
