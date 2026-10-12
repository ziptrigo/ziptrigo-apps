import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from apps.accounts.models import User

if TYPE_CHECKING:
    from django.db.models import Manager


class InboundMessageStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    DONE = 'done', 'Done'
    FAILED = 'failed', 'Failed'


class InboundMessage(models.Model):
    """One row per S3 object found under `receipts/inbound/` (SES writes one object per received
    message, named after its message id). It makes polling the prefix idempotent -- a key that
    already has a row is never ingested twice -- and failures are visible in the admin and with
    SQL. The poller and the ingest task that fill and consume it arrive in a later release."""

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    emails: ClassVar['Manager']

    s3_key = cast(str, models.CharField(max_length=512, unique=True))
    status = cast(
        str,
        models.CharField(
            max_length=16,
            choices=InboundMessageStatus.choices,
            default=InboundMessageStatus.PENDING,
        ),
    )
    attempts = cast(int, models.PositiveSmallIntegerField(default=0))
    last_error = cast(str, models.TextField(blank=True, default=''))
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))
    processed_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))

    class Meta:
        indexes = [models.Index(fields=['status', 'created_at'])]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.s3_key} ({self.status})'


class InboundEmailStatus(models.TextChoices):
    ACCEPTED = 'accepted', 'Accepted'
    REJECTED_SENDER = 'rejected_sender', 'Rejected: sender not allowed'
    REJECTED_SPAM = 'rejected_spam', 'Rejected: flagged as spam'
    REJECTED_VIRUS = 'rejected_virus', 'Rejected: flagged as a virus'
    REJECTED_CAP = 'rejected_cap', 'Rejected: daily limit reached'
    REJECTED_TOO_LARGE = 'rejected_too_large', 'Rejected: too large'
    FORWARDING_CONFIRMATION = 'forwarding_confirmation', 'Forwarding confirmation'
    NO_RECEIPT = 'no_receipt', 'No receipt found'


class InboundEmail(models.Model):
    """One row per matched recipient of an `InboundMessage` (one message can reach several
    users). A rejected email keeps only this metadata, so the user can see what was dropped.

    `raw_key` is the stripped `.eml` (attachment payloads removed) under the user's prefix and
    `size_bytes` is that object's size -- both empty/zero when nothing was stored. The `.eml` is
    deleted with the email's last receipt (`services.lifecycle.purge_receipt`).
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    receipts: ClassVar['Manager']

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='inbound_emails'
        ),
    )
    user_id: UUID
    message = models.ForeignKey(InboundMessage, on_delete=models.CASCADE, related_name='emails')
    recipient = cast(str, models.CharField(max_length=320))
    from_address = cast(str, models.CharField(max_length=320, blank=True, default=''))
    envelope_sender = cast(str, models.CharField(max_length=320, blank=True, default=''))
    subject = cast(str, models.CharField(max_length=500, blank=True, default=''))
    received_at = cast(datetime | None, models.DateTimeField(null=True, blank=True))
    raw_key = cast(str, models.CharField(max_length=512, blank=True, default=''))
    size_bytes = cast(int, models.PositiveBigIntegerField(default=0))
    status = cast(
        str,
        models.CharField(
            max_length=32,
            choices=InboundEmailStatus.choices,
            default=InboundEmailStatus.ACCEPTED,
        ),
    )
    attention_notified_at = cast(
        datetime | None,
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='When the "needs your attention" email was queued for this email, so it '
            'is sent at most once.',
        ),
    )
    created_at = cast(datetime | None, models.DateTimeField(auto_now_add=True))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['message', 'recipient'], name='receipts_inboundemail_message_recipient'
            )
        ]
        indexes = [models.Index(fields=['user', '-created_at'])]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.recipient} ({self.status})'
