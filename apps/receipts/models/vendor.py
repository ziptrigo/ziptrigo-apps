from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models.functions import Lower

from apps.accounts.models import User

if TYPE_CHECKING:
    from django.db.models import Manager

MAX_VENDOR_NAME_LENGTH = 255


class Vendor(models.Model):
    """One of a user's vendors. Its `name` is what every receipt linked to it shows (the text is
    kept in `Receipt.vendor`, which a rename or a merge rewrites in one `UPDATE`).

    A vendor the pipeline creates starts unvetted (`vetted_at` is null). It becomes vetted when
    the user confirms it: renames it, types or picks it on a receipt, merges others into it, or
    marks it vetted. Matching (`services.vendors.match_vendor`) prefers a vetted vendor over an
    unvetted one. Vendors are per user, like categories; nothing is shared.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='receipt_vendors'
        ),
    )
    user_id: UUID
    name = cast(str, models.CharField(max_length=MAX_VENDOR_NAME_LENGTH))
    vetted_at = cast(
        'datetime | None',
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='When the user confirmed this vendor; empty while it is only what the AI '
            'read.',
        ),
    )
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    updated_at = cast(datetime, models.DateTimeField(auto_now=True))

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(Lower('name'), 'user', name='receipts_vendor_user_name_ci'),
        ]
        indexes = [models.Index(fields=['user', 'vetted_at'])]

    def __str__(self) -> str:
        return self.name

    @property
    def vetted(self) -> bool:
        return self.vetted_at is not None


class VendorAliasSource(models.TextChoices):
    EXTRACTED = 'extracted', 'Read by the AI'
    USER = 'user', 'Set by the user'  # a vendor name, a rename, a receipt moved by hand
    MERGE = 'merge', 'Merged'


class VendorAlias(models.Model):
    """A name `vendor` is known by, under its normalized `key` (`services.vendors.normalize_key`).
    A key belongs to at most one vendor per user, which is what makes matching deterministic.

    `user` repeats `vendor.user` only so that rule can be a database constraint."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    user = cast(
        'User',
        models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+'),
    )
    user_id: UUID
    vendor = cast(
        'Vendor',
        models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='aliases'),
    )
    vendor_id: int
    key = cast(str, models.CharField(max_length=MAX_VENDOR_NAME_LENGTH))
    name = cast(
        str,
        models.CharField(
            max_length=MAX_VENDOR_NAME_LENGTH, help_text='The spelling first seen, for display.'
        ),
    )
    source = cast(str, models.CharField(max_length=16, choices=VendorAliasSource.choices))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        verbose_name_plural = 'vendor aliases'
        constraints = [
            models.UniqueConstraint(fields=['user', 'key'], name='receipts_vendor_alias_user_key'),
        ]

    def __str__(self) -> str:
        return f'{self.name} -> {self.vendor_id}'
