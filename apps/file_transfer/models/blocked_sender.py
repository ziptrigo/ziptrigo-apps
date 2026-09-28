import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone

from apps.accounts.models import User

if TYPE_CHECKING:
    from django.db.models import Manager


class BlockedSenderKind(models.TextChoices):
    """What `BlockedSender.value` holds (issue #59): an email (exact, case-insensitive, or a
    `*@domain` wildcard) or an IP address/CIDR range (IPv4 or IPv6)."""

    EMAIL = 'email', 'Email'
    IP = 'ip', 'IP / CIDR'


class BlockedSender(models.Model):
    """A sender email or IP/CIDR that may not send transfers (issue #59). Checked by
    `apps.file_transfer.services.blocklist` at every transfer-creation and anonymous-confirmation
    checkpoint -- see that module's docstring for the full list.

    `value` for `kind=EMAIL` is either an exact address (`someone@example.com`) or a domain
    wildcard (`*@example.com`, blocking every address at that domain); for `kind=IP` it's a bare
    address or a CIDR range (`203.0.113.0/24`, `2001:db8::/32`), covering both IPv4 and IPv6.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = cast(str, models.CharField(max_length=8, choices=BlockedSenderKind.choices))
    value = cast(
        str,
        models.CharField(
            max_length=255,
            help_text='An exact email or "*@domain" wildcard (kind=Email), or an IP address or '
            'CIDR range (kind=IP/CIDR).',
        ),
    )
    reason = cast(str, models.TextField(blank=True, default=''))
    created_by = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='+',
        ),
    )
    created_by_id: UUID | None
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    expires_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True, blank=True, help_text='Blank means this block never expires.'
        ),
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['kind', 'value'])]

    def __str__(self) -> str:
        # `BlockedSenderKind(self.kind).label` rather than the auto-generated
        # `get_kind_display()`: that method exists at runtime (Django adds it for every
        # `choices=` field) but only via metaclass magic `ty` can't see -- same category of gap
        # as `.objects`/`.DoesNotExist`, see the class-level comment in
        # `apps/qr_code/models/qrcode.py`.
        return f'{BlockedSenderKind(self.kind).label}: {self.value}'

    def save(self, *args, **kwargs) -> None:
        # Normalise at write time so every check site can compare case-insensitively for free
        # (email addresses and hostnames are case-insensitive; an IP/CIDR string has no case to
        # normalise but stripping whitespace is still worth doing).
        self.value = self.value.strip()
        if self.kind == BlockedSenderKind.EMAIL:
            self.value = self.value.lower()
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())
