from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

if TYPE_CHECKING:
    from django.db.models import Manager

#: Seeded for every new account, in this order.
DEFAULT_CATEGORIES = (
    'Advertising & marketing',
    'Bank & payment fees',
    'Equipment',
    'Meals & entertainment',
    'Office supplies',
    'Professional services',
    'Rent & utilities',
    'Software & subscriptions',
    'Phone & internet',
    'Travel',
    'Vehicle & fuel',
    'Other',
)

MAX_CATEGORY_NAME_LENGTH = 100


class ReceiptCategory(models.Model):
    """One entry of a user's own category list; the AI picks from it."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    # `ty` types `pk` as the primary key field object itself, not the value it holds.
    pk: int

    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='receipt_categories'
        ),
    )
    user_id: UUID
    name = cast(str, models.CharField(max_length=MAX_CATEGORY_NAME_LENGTH))
    sort_order = cast(int, models.PositiveIntegerField(default=0))

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'receipt categories'
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='receipts_category_user_name')
        ]

    def __str__(self) -> str:
        return self.name
