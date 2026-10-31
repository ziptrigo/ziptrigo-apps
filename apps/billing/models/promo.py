"""Promo codes (issue #70): a code a user redeems for credits, and the record of each redemption.

A `PromoCode` grants a fixed number of credits (`effect = credits`, the only effect so far: there
is no purchase flow yet, #54, so a discount has nothing to attach to; the field is the hook for
percent-off or bonus-on-purchase effects later). It may be scheduled (`starts_at`/`ends_at`),
capped (`max_redemptions`), bound to a few email addresses or to new accounts, and is always once
per user. A `PromoRedemption` is one user's use of one code: `granted` once the credits landed, or
`pending` while a code entered at signup waits for the email to be confirmed (that reservation
holds one of the code's uses, `claimed_count`, and is `released` if the account never confirms).
The rules live in `apps.billing.services.promo`; nothing here is changed by hand.
"""

import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

if TYPE_CHECKING:
    from django.db.models import Manager

    from .credit_transaction import CreditTransaction

#: The shape of a code as stored: upper-case letters, digits and hyphens, 4 to 32 characters.
#: Codes are compared case-insensitively by storing them upper-cased (`services.promo.normalize_code`).
CODE_MAX_LENGTH = 32
CODE_PATTERN = re.compile(r'^[A-Z0-9-]{4,32}$')
CAMPAIGN_MAX_LENGTH = 100
#: Bounds on what one code grants.
MIN_CREDITS = 1
MAX_CREDITS = 1_000_000


class PromoCodeEffect(models.TextChoices):
    """What redeeming a code does. Only `credits` exists until there is a purchase to discount."""

    CREDITS = 'credits', 'Credits'


class PromoCode(models.Model):
    """A promo code. Created by staff in the admin; redeemed through `services.promo`."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    # The reverse accessor of `PromoRedemption.code`; Django's metaclass adds it, `ty` can't see it.
    redemptions: ClassVar['Manager']

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = cast(
        str,
        models.CharField(
            max_length=CODE_MAX_LENGTH,
            unique=True,
            help_text='Letters, digits and hyphens, 4-32 characters; stored upper-case and matched '
            'ignoring case. Leave empty to generate one.',
        ),
    )
    effect = cast(
        str,
        models.CharField(
            max_length=16, choices=PromoCodeEffect.choices, default=PromoCodeEffect.CREDITS
        ),
    )
    credits = cast(
        int,
        models.PositiveIntegerField(
            validators=[MinValueValidator(MIN_CREDITS), MaxValueValidator(MAX_CREDITS)],
            help_text='Credits added to the balance on redemption.',
        ),
    )
    campaign = cast(
        str,
        models.CharField(
            max_length=CAMPAIGN_MAX_LENGTH,
            blank=True,
            default='',
            help_text='What this code is for (an event, a partner, a launch); shown in the ledger.',
        ),
    )
    notes = cast(str, models.TextField(blank=True, default='', help_text='Staff-only notes.'))
    active = cast(
        bool,
        models.BooleanField(
            default=True, help_text='Off: nobody can redeem it, whatever the dates.'
        ),
    )
    starts_at = cast(
        datetime | None,
        models.DateTimeField(null=True, blank=True, help_text='Redeemable from this moment.'),
    )
    ends_at = cast(
        datetime | None,
        models.DateTimeField(null=True, blank=True, help_text='Not redeemable from this moment.'),
    )
    max_redemptions = cast(
        int | None,
        models.PositiveIntegerField(
            null=True, blank=True, help_text='Across all users; empty means unlimited.'
        ),
    )
    #: Granted redemptions plus pending signup reservations: what `max_redemptions` is compared
    #: with, bumped with one conditional `UPDATE` so racing redemptions can't overshoot the cap.
    claimed_count = cast(int, models.PositiveIntegerField(default=0, editable=False))
    new_accounts_within_days = cast(
        int | None,
        models.PositiveIntegerField(
            null=True,
            blank=True,
            help_text='Only accounts created within this many days may redeem; empty means any.',
        ),
    )
    #: Lower-cased addresses; empty means anyone. A code bound to addresses answers "Invalid code"
    #: to everyone else, so a guesser can't learn whose it is.
    allowed_emails = cast(
        list[str],
        models.JSONField(
            default=list,
            blank=True,
            help_text='Only these addresses may redeem; empty means anyone.',
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='+',
    )
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    updated_at = cast(datetime | None, models.DateTimeField(auto_now=True))

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Promo code'
        indexes = [models.Index(fields=['campaign'])]

    def __str__(self) -> str:
        return self.code

    @property
    def is_exhausted(self) -> bool:
        return self.max_redemptions is not None and self.claimed_count >= self.max_redemptions

    @property
    def is_claimed(self) -> bool:
        """Whether anyone has redeemed or reserved it: then its code and credits are frozen."""
        return self.claimed_count > 0


class PromoRedemptionStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    GRANTED = 'granted', 'Granted'
    RELEASED = 'released', 'Released'


class PromoRedemptionSource(models.TextChoices):
    FORM = 'form', 'Redeem form'
    SIGNUP = 'signup', 'Sign-up'


class PromoRedemption(models.Model):
    """One user's use of one code. `pending` holds a use until the email is confirmed (a code
    entered at signup), `granted` means the credits landed (`transaction` is the ledger row),
    `released` means a reservation was given back (the account never confirmed)."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # `PROTECT`: a code with redemptions can only be deactivated, never deleted (the admin refuses
    # it first; this is the backstop).
    code = cast(
        PromoCode,
        models.ForeignKey(PromoCode, on_delete=models.PROTECT, related_name='redemptions'),
    )
    code_id: uuid.UUID
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='promo_redemptions'
    )
    user_id: uuid.UUID
    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=PromoRedemptionStatus.choices,
            default=PromoRedemptionStatus.PENDING,
        ),
    )
    source = cast(str, models.CharField(max_length=16, choices=PromoRedemptionSource.choices))
    credits = cast(int, models.PositiveIntegerField(help_text='What the code granted at the time.'))
    transaction = cast(
        'CreditTransaction | None',
        models.ForeignKey(
            'billing.CreditTransaction',
            on_delete=models.SET_NULL,
            null=True,
            blank=True,
            related_name='+',
        ),
    )
    transaction_id: uuid.UUID | None
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    granted_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    released_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Promo redemption'
        constraints = [
            # Once per user per code; a released reservation doesn't count, so a user whose signup
            # reservation lapsed may still redeem the code on the form.
            models.UniqueConstraint(
                fields=['code', 'user'],
                condition=Q(
                    status__in=[PromoRedemptionStatus.PENDING, PromoRedemptionStatus.GRANTED]
                ),
                name='billing_promo_once_per_user',
            ),
        ]
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.code_id} {self.user_id} {self.status}'
