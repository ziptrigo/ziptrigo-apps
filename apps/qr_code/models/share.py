import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from ..limits import LINK_LABEL_MAX, MAX_DETAILS_LENGTH
from .qrcode import QRCode

if TYPE_CHECKING:
    from django.db.models import Manager

    from apps.accounts.models import User


class QRShareLink(models.Model):
    """A capability link to a QR code: `<BASE_URL>/q/<code>` shows it to anyone who has the address.

    The code (`secrets.token_urlsafe(16)`, 22 characters) is the whole secret: it is never reused,
    never editable and, in the admin, shown only by its first 6 characters. A link is *active*
    while it is not revoked and has not expired; whether it *works* also depends on the QR code
    (`services.share.is_available`: not deleted). A revoked link is never re-activated. The link
    shows the QR code as it is now, not a snapshot. `label` is the owner's own reminder of who it
    is for; it is never shown publicly.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: uuid.UUID
    qrcode_id: uuid.UUID
    # The reverse accessor of `QRShareRecipient.link` (the people the link was emailed to) and of
    # `QRShareReport.link` (the reports made from this link's page).
    recipients: 'Manager'
    reports: 'Manager'
    # Set by `services.share.list_links` for the owner's panel (not stored).
    url: str
    state: str
    expiry_value: str
    expires_field_id: str

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = cast(
        'QRCode',
        models.ForeignKey(QRCode, on_delete=models.CASCADE, related_name='share_links'),
    )
    code = cast(str, models.CharField(max_length=64, unique=True, editable=False))
    label = cast(str, models.CharField(max_length=LINK_LABEL_MAX, blank=True))
    expires_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    revoked_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        indexes = [models.Index(fields=['qrcode', 'revoked_at'], name='qr_share_link_qr_idx')]
        verbose_name = 'QR share link'
        verbose_name_plural = 'QR share links'

    def __str__(self) -> str:
        return f'{self.code[:6]}...'

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or timezone.now())

    def is_active(self, now: datetime | None = None) -> bool:
        """Not revoked and not expired (the QR code's own state is not looked at)."""
        return not self.is_revoked and not self.is_expired(now)


class QRShareRecipient(models.Model):
    """A person a share link was emailed to (issue #5): the link, their address and when they were
    last emailed, which drives the resend cooldown. The address is stored with its domain
    lower-cased and the local part as typed; a person is one row per link, ignoring case. Deleted
    with the link (and so with the QR code and the owner)."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: uuid.UUID
    link_id: uuid.UUID

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = cast(
        'QRShareLink',
        models.ForeignKey(QRShareLink, on_delete=models.CASCADE, related_name='recipients'),
    )
    email = cast(str, models.EmailField())
    last_sent_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower('email'), 'link', name='qr_share_recipient_unique')
        ]
        verbose_name = 'QR share recipient'
        verbose_name_plural = 'QR share recipients'

    def __str__(self) -> str:
        return self.email


class ReportReason(models.TextChoices):
    """Reason categories of the public "report this QR code" form (Notes' and Short URLs' set)."""

    PHISHING = 'phishing', 'Phishing or scam'
    MALWARE = 'malware', 'Malware'
    SPAM = 'spam', 'Spam'
    ILLEGAL_CONTENT = 'illegal_content', 'Illegal content'
    COPYRIGHT = 'copyright', 'Copyright infringement'
    OTHER = 'other', 'Other'


class ReportStatus(models.TextChoices):
    """A report's review state: `PENDING` until staff act on it, then `ACTIONED` (its QR code was
    taken down) or `DISMISSED` (staff decided no action was needed)."""

    PENDING = 'pending', 'Pending'
    ACTIONED = 'actioned', 'Actioned'
    DISMISSED = 'dismissed', 'Dismissed'


class QRShareReport(models.Model):
    """One "report this QR code" submission from a shared page. No login is needed to send one.
    `reporter_email` is optional; `reporter_ip` is the best-effort client address
    (`apps.core.services.client_ip`), kept for support and abuse purposes, as Notes and Short URLs
    keep their reporters'. The owner never sees who reported a code."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    pk: uuid.UUID
    qrcode_id: uuid.UUID
    link_id: uuid.UUID | None
    reviewed_by_id: uuid.UUID | None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qrcode = cast(
        'QRCode',
        models.ForeignKey(QRCode, on_delete=models.CASCADE, related_name='share_reports'),
    )
    link = cast(
        'QRShareLink | None',
        models.ForeignKey(
            QRShareLink,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='reports',
            help_text='The link the report was sent from.',
        ),
    )
    reason = cast(str, models.CharField(max_length=32, choices=ReportReason))
    details = cast(str, models.TextField(blank=True, default='', max_length=MAX_DETAILS_LENGTH))
    reporter_email = cast(str, models.EmailField(blank=True, default=''))
    reporter_ip = cast('str | None', models.GenericIPAddressField(null=True, blank=True))
    status = cast(
        str,
        models.CharField(max_length=16, choices=ReportStatus, default=ReportStatus.PENDING),
    )
    reviewed_by = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='+',
            help_text='The staff user who dismissed this report or took its QR code down.',
        ),
    )
    reviewed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    resolution_note = cast(str, models.TextField(blank=True, default=''))
    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['qrcode', 'status'], name='qr_share_report_qr_idx'),
            models.Index(fields=['status', 'created_at'], name='qr_share_report_status_idx'),
        ]
        verbose_name = 'QR share report'
        verbose_name_plural = 'QR share reports'

    def __str__(self) -> str:
        return f'Report on {self.qrcode_id} ({self.status})'
