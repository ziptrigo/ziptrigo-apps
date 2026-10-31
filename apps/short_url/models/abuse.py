import ipaddress
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .link import ShortLink

if TYPE_CHECKING:
    from django.db.models import Manager

    from apps.accounts.models import User

#: Free-text "details" field cap on a report.
MAX_DETAILS_LENGTH = 2000


class ReportReason(models.TextChoices):
    """Reason categories of the public "report this link" form."""

    PHISHING = 'phishing', 'Phishing or scam'
    MALWARE = 'malware', 'Malware'
    SPAM = 'spam', 'Spam'
    ILLEGAL_CONTENT = 'illegal_content', 'Illegal content'
    COPYRIGHT = 'copyright', 'Copyright infringement'
    OTHER = 'other', 'Other'


class ReportStatus(models.TextChoices):
    """A report's review state: `PENDING` until staff act on it, then `ACTIONED` (its link was
    taken down) or `DISMISSED` (staff decided no action was needed)."""

    PENDING = 'pending', 'Pending'
    ACTIONED = 'actioned', 'Actioned'
    DISMISSED = 'dismissed', 'Dismissed'


class ReportSource(models.TextChoices):
    """Who made a report: a visitor, through the public form, or the periodic Safe Browsing
    recheck (`services.reputation`), whose reports have no reporter and are never counted
    toward the visitors' auto-hold threshold."""

    VISITOR = 'visitor', 'Visitor'
    SAFE_BROWSING = 'safe_browsing', 'Safe Browsing'


class LinkReport(models.Model):
    """One "report this link" submission from the public preview page. No login is needed to
    send one. `reporter_email` is optional; `reporter_ip` is the best-effort client address
    (`apps.core.services.client_ip`), kept for support and abuse purposes, as file transfer keeps
    its reporters'. The owner never sees who reported a link."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    link_id: uuid.UUID
    reviewed_by_id: uuid.UUID | None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = cast(
        ShortLink,
        models.ForeignKey(ShortLink, on_delete=models.CASCADE, related_name='reports'),
    )
    reason = cast(str, models.CharField(max_length=32, choices=ReportReason.choices))
    details = cast(str, models.TextField(blank=True, default='', max_length=MAX_DETAILS_LENGTH))
    reporter_email = cast(str, models.EmailField(blank=True, default=''))
    reporter_ip = cast('str | None', models.GenericIPAddressField(null=True, blank=True))

    source = cast(
        str,
        models.CharField(
            max_length=16,
            choices=ReportSource.choices,
            default=ReportSource.VISITOR,
            db_default=ReportSource.VISITOR,
        ),
    )
    status = cast(
        str,
        models.CharField(max_length=16, choices=ReportStatus.choices, default=ReportStatus.PENDING),
    )
    reviewed_by = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='+',
            help_text='The staff user who dismissed this report or took its link down.',
        ),
    )
    reviewed_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    resolution_note = cast(str, models.TextField(blank=True, default=''))

    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['link', 'status'], name='short_report_link_idx'),
            models.Index(fields=['status', 'created_at'], name='short_report_status_idx'),
        ]

    def __str__(self) -> str:
        return f'Report on {self.link_id} ({self.status})'


class BlockedDomain(models.Model):
    """A destination host that short links may not point at, managed in the admin.

    `value` is either an **exact host** (`evil.example.com`: only that host) or a **wildcard**
    (`*.example.com`: `example.com` itself *and* every subdomain of it, however deep). The
    wildcard covers the bare host on purpose: blocking a domain should take one entry, not two,
    and a block that missed the bare name would be the first thing someone tries. A wildcard needs
    at least two labels (`*.com` would block a whole top-level domain, which is never intended;
    a public suffix such as `*.co.uk` is not detected, so take care). An IP-literal host can only
    be blocked exactly.

    Enforced by `apps.short_url.services.blocklist`: at create and destination edit (a neutral
    refusal), and at every public request, where a link whose destination is blocked renders the
    unavailable page -- so a block takes effect on links that already exist.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    created_by_id: uuid.UUID | None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    value = cast(
        str,
        models.CharField(
            max_length=255,
            help_text='An exact host ("evil.example.com") or "*.example.com", which blocks '
            'example.com and every subdomain of it. Written in lower case, internationalized '
            'names as their ASCII (punycode) form.',
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
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    expires_at = cast(
        'datetime | None',
        models.DateTimeField(
            null=True, blank=True, help_text='Blank means this block never expires.'
        ),
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['value'], name='short_blocked_value_idx')]

    def __str__(self) -> str:
        return self.value

    def save(self, *args, **kwargs) -> None:
        # Normalised at write time so the check can compare plainly. An invalid value raises
        # (`ValidationError`) rather than being stored in a form no host can ever match, which
        # would be a block that silently blocks nothing; the admin form shows `clean()`'s error
        # before it ever gets here.
        self.value = normalize_blocked_value(self.value)
        super().save(*args, **kwargs)

    def clean(self) -> None:
        """Validate and normalise `value`, and refuse a second *active* entry for it (an expired
        duplicate is fine: that is the value's block history)."""
        super().clean()
        self.value = normalize_blocked_value(self.value)
        now = timezone.now()
        duplicate = (
            BlockedDomain.objects.filter(value=self.value)
            .exclude(pk=self.pk)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .exists()
        )
        if duplicate:
            raise ValidationError(
                {
                    'value': 'This value is already actively blocked -- edit that entry '
                    'instead of adding a duplicate.'
                }
            )

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())


def normalize_blocked_value(raw: str) -> str:
    """The stored form of a blocklist value, or a `ValidationError` keyed `value`: stripped and
    lower-cased, a trailing dot dropped, the host IDNA-encoded, an IP address compressed. A
    wildcard is `*.` plus a host of at least two labels (never an IP address)."""
    error = ValidationError({'value': 'Enter a host name ("example.com") or "*.example.com".'})
    value = (raw or '').strip().lower()
    wildcard = value.startswith('*.')
    host = value[2:] if wildcard else value
    host = host.rstrip('.')
    if not host:
        raise error

    try:
        ip = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        pass
    else:
        if wildcard:
            raise ValidationError({'value': 'An IP address can only be blocked exactly.'})
        return str(ip)

    if any(char in host for char in '*/:@?#\\ []'):
        raise error

    try:
        ascii_host = host.encode('idna').decode('ascii')
    except UnicodeError:
        raise error from None
    labels = ascii_host.split('.')
    if len(ascii_host) > 253 or not all(
        label and len(label) <= 63 and label[0] != '-' and label[-1] != '-' for label in labels
    ):
        raise error
    if not all(char.isalnum() or char in '-_' for char in ascii_host.replace('.', '')):
        raise error
    if wildcard and len(labels) < 2:
        raise ValidationError(
            {'value': 'A wildcard needs a domain of at least two parts ("*.example.com").'}
        )
    return f'*.{ascii_host}' if wildcard else ascii_host
