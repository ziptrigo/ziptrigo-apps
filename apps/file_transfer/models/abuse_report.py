import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

from .transfer import Transfer

if TYPE_CHECKING:
    from django.db.models import Manager

#: Free-text "details" field cap (issue #59: "free-text details with a max length").
MAX_DETAILS_LENGTH = 2000


class AbuseReportReason(models.TextChoices):
    """Reason categories for the public "report this transfer" form (issue #59)."""

    MALWARE = 'malware', 'Malware'
    COPYRIGHT = 'copyright', 'Copyright infringement'
    ILLEGAL_CONTENT = 'illegal_content', 'Illegal content'
    PHISHING_SPAM = 'phishing_spam', 'Phishing or spam'
    OTHER = 'other', 'Other'


class AbuseReportStatus(models.TextChoices):
    """A report's review state (issue #59): `PENDING` until staff act on it, then either
    `ACTIONED` (its transfer was taken down) or `DISMISSED` (staff decided no action was needed)."""

    PENDING = 'pending', 'Pending'
    ACTIONED = 'actioned', 'Actioned'
    DISMISSED = 'dismissed', 'Dismissed'


class AbuseReport(models.Model):
    """One "report this transfer" submission from the public download page (issue #59). No login
    required to submit one -- `reporter_email` is optional and `reporter_ip` is the best-effort
    client IP (`apps.core.services.client_ip`), recorded for support/abuse purposes the same way
    `DownloadEvent.ip` is (spec section 12: not purged by `purge_download_ips`, since a report is a
    support record, not a download log entry).
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = cast(
        Transfer,
        models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name='reports'),
    )
    transfer_id: UUID

    reason = cast(str, models.CharField(max_length=32, choices=AbuseReportReason.choices))
    details = cast(str, models.TextField(blank=True, default='', max_length=MAX_DETAILS_LENGTH))
    reporter_email = cast(str, models.EmailField(blank=True, default=''))
    reporter_ip = cast('str | None', models.GenericIPAddressField(null=True, blank=True))

    status = cast(
        str,
        models.CharField(
            max_length=16, choices=AbuseReportStatus.choices, default=AbuseReportStatus.PENDING
        ),
    )
    reviewed_by = cast(
        'User | None',
        models.ForeignKey(
            settings.AUTH_USER_MODEL,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='+',
            help_text='The staff user who dismissed this report or took its transfer down.',
        ),
    )
    reviewed_by_id: UUID | None
    reviewed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    resolution_note = cast(str, models.TextField(blank=True, default=''))

    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['transfer', 'status']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self) -> str:
        return f'Report on {self.transfer_id} ({self.status})'
