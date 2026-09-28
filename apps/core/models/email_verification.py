import uuid
from datetime import datetime
from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.db import models


class EmailVerification(models.Model):
    """One row per confirm-this-email attempt, shared by any app that needs to prove someone
    controls an email address without necessarily having a user for it yet (issue #58):
    `accounts`' signup/email-change confirmation today, `file_transfer`'s anonymous-sender
    confirmation in a later phase. See `apps.core.services.email_verification` for the state
    machine, the hashing, and why `core` -- which can't import `accounts` -- owns this at all.

    `purpose` is a free string the caller picks (e.g. `'accounts.email_confirmation'`); `core`
    never interprets it, only uses it (together with `email`) to scope "only the latest
    verification is valid" and the resend cooldown to the right set of rows.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = cast(str, models.EmailField())
    purpose = cast(str, models.CharField(max_length=100))

    # Hashed, never the raw code/token -- see `apps.core.services.email_verification` for how and
    # why (HMAC-SHA256, keyed with `SECRET_KEY`, compared in constant time).
    code_hash = cast(str, models.CharField(max_length=64))
    # `unique=True` (not just `db_index=True`): `confirm_by_token` looks a row up by this alone,
    # so two rows sharing a hash would make that lookup ambiguous. A collision would need either
    # a second `secrets.token_urlsafe(32)` draw landing on the same 32 bytes, or a HMAC-SHA256
    # collision -- vanishingly unlikely, but the constraint costs nothing and removes the
    # possibility outright rather than relying on luck.
    token_hash = cast(str, models.CharField(max_length=64, unique=True))

    expires_at = cast(datetime, models.DateTimeField())
    attempts = cast(int, models.PositiveSmallIntegerField(default=0))
    confirmed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    invalidated_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text=(
                'Set when a newer verification for the same email+purpose supersedes this one.'
            ),
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Email verification'
        verbose_name_plural = 'Email verifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email', 'purpose', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.email} ({self.purpose})'
