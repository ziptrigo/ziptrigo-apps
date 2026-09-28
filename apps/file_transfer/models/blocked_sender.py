import ipaddress
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db import models
from django.db.models import Q
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
            help_text='An exact email or "*@domain" wildcard (kind=Email; matches only that '
            "exact domain, not its subdomains -- '*@example.com' does not match "
            "'someone@mail.example.com'), or an IP address or CIDR range (kind=IP/CIDR).",
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

    def clean(self) -> None:
        """Validate `value` against `kind` (issue #59 code review: an unparseable CIDR was
        silently ignored by `services.blocklist._ip_blocked`, and an email value missing its
        `*@` prefix -- `example.com` instead of `*@example.com` -- would just never match
        anything, both without any feedback that the entry was useless), and refuse a second
        *active* entry for the same `(kind, value)` -- letting the admin CRUD form create one is
        exactly the "duplicate entries" case `services.blocklist.block_transfer_sender` guards
        against on its own path (see its `_block` helper); an *expired* duplicate is fine, since
        that's just the value's block history.

        Runs through `ModelForm.full_clean()` (the admin's own add/change form), not on a plain
        `.save()` -- `services.blocklist` writes through `BlockedSender.objects.create()`/
        `.save(update_fields=[...])` directly and enforces the same "active" rule itself, since a
        service function raising a model-validation `ValidationError` deep inside a bulk admin
        action isn't the right UX there.
        """
        super().clean()
        value = (self.value or '').strip()
        if self.kind == BlockedSenderKind.EMAIL:
            value = value.lower()
            self.value = value
            self._validate_email_value(value)
        elif self.kind == BlockedSenderKind.IP:
            self.value = value
            self._validate_ip_value(value)

        if value and self._has_active_duplicate(value):
            raise ValidationError(
                {
                    'value': 'This value is already actively blocked -- edit that entry '
                    'instead of adding a duplicate.'
                }
            )

    def _validate_email_value(self, value: str) -> None:
        error = {'value': 'Enter a valid email address, or a "*@domain" wildcard.'}
        if not value:
            raise ValidationError(error)
        if value.startswith('*@'):
            domain = value[2:]
            if not domain or '@' in domain:
                raise ValidationError(error)
            try:
                validate_email(f'placeholder@{domain}')
            except ValidationError:
                raise ValidationError(error) from None
            return
        try:
            validate_email(value)
        except ValidationError:
            raise ValidationError(error) from None

    def _validate_ip_value(self, value: str) -> None:
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError:
            raise ValidationError({'value': 'Enter a valid IP address or CIDR range.'}) from None

    def _has_active_duplicate(self, value: str) -> bool:
        now = timezone.now()
        return (
            BlockedSender.objects.filter(kind=self.kind, value=value)
            .exclude(pk=self.pk)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .exists()
        )

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())
