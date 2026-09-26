from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

if TYPE_CHECKING:
    from django.db.models import Manager


class CreditAccount(models.Model):
    """A user's credit balance, shared by every app on the site.

    The balance is only ever changed through `apps.billing.services.credits`, which also records a
    `CreditTransaction` for each change. Created lazily, on the first change: a user without one
    has a balance of zero.
    """

    # Not explicitly assigned -- Django's `ModelBase` metaclass injects both of these; see the
    # matching comment in `apps/accounts/models/user.py`.
    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='credit_account',
    )
    # Django adds this `_id` companion attribute automatically for every `ForeignKey`; it's never
    # declared as a field itself, so `ty` has no way to know it exists without this annotation.
    user_id: UUID
    # See the matching comment in `apps/accounts/models/user.py` for why these are `cast`.
    balance = cast(int, models.PositiveBigIntegerField(default=0, help_text='Current balance.'))
    updated_at = cast(datetime | None, models.DateTimeField(auto_now=True))

    def __str__(self) -> str:
        return f'{self.user_id}: {self.balance}'
