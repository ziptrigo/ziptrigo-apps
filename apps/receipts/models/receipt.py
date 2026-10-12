import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MaxLengthValidator
from django.db import models

from apps.accounts.models import User

from .category import ReceiptCategory
from .inbound import InboundEmail

if TYPE_CHECKING:
    from django.db.models import Manager

MAX_NOTES_LENGTH = 5000


class ReceiptStatus(models.TextChoices):
    """`RECEIVED` (stored, waiting for a worker, or between retries) -> `PROCESSING` -> one of
    `PROCESSED`, `NEEDS_REVIEW` or `FAILED`. `WAITING_FOR_CREDITS` is where a receipt waits when
    the owner can't pay for processing (or the account is frozen); it is picked up again on a
    top-up. Trash is not a status: it is `Receipt.deleted_at`."""

    RECEIVED = 'received', 'Received'
    PROCESSING = 'processing', 'Processing'
    WAITING_FOR_CREDITS = 'waiting_for_credits', 'Waiting for credits'
    NEEDS_REVIEW = 'needs_review', 'Needs review'
    PROCESSED = 'processed', 'Processed'
    FAILED = 'failed', 'Failed'


#: Statuses in which the AI pipeline still owns the receipt (user edits of extracted fields are
#: refused while it does, so they cannot race the result being applied).
IN_PROGRESS_STATUSES = (
    ReceiptStatus.RECEIVED,
    ReceiptStatus.PROCESSING,
    ReceiptStatus.WAITING_FOR_CREDITS,
)

#: Statuses that are settled: nothing is going to change them without the user acting.
SETTLED_STATUSES = (ReceiptStatus.NEEDS_REVIEW, ReceiptStatus.PROCESSED, ReceiptStatus.FAILED)


class FailureReason(models.TextChoices):
    MULTIPLE_RECEIPTS = 'multiple_receipts', 'Multiple receipts in one file'
    NOT_A_RECEIPT = 'not_a_receipt', 'Not a receipt or invoice'
    UNREADABLE = 'unreadable', 'Unreadable file'
    UNSUPPORTED_TYPE = 'unsupported_type', 'Unsupported file type'
    TOO_LARGE = 'too_large', 'File too large'
    EXTRACTION_ERROR = 'extraction_error', 'Extraction error'


class ReceiptSource(models.TextChoices):
    EMAIL = 'email', 'Email'
    UPLOAD = 'upload', 'Upload'
    API = 'api', 'API'


class DocumentType(models.TextChoices):
    RECEIPT = 'receipt', 'Receipt'
    INVOICE = 'invoice', 'Invoice'


#: The fields that make a receipt *Processed* when all are set; any missing -> Needs review.
REQUIRED_FIELDS = ('vendor', 'amount', 'receipt_date')

#: The fields the AI fills and the user may edit (and which `user_edited_fields` can name). `notes`
#: is deliberately not here: it is the user's alone.
EXTRACTED_FIELDS = (
    'vendor',
    'amount',
    'currency',
    'receipt_date',
    'document_type',
    'document_number',
    'payment_method',
    'category',
)


class Receipt(models.Model):
    """One receipt or invoice: one stored file (an upload, an API upload, an email attachment, or
    an email body) and what was extracted from it.

    `receipt_date` is the date on the document; `created_at` is when the row was added -- they
    differ whenever a receipt is emailed days later. A receipt is *Processed* only when `vendor`,
    `amount` and `receipt_date` are all set (they are nullable/blank until then).

    Processing state: `attempt_id` identifies one paid extraction; a charge is recorded in
    `charged_attempt_id`, so an at-least-once re-run of the same attempt never charges twice, and
    a user re-run takes a fresh `attempt_id` (and is charged again). `credits_charged` is what the
    *current* attempt cost (the refund amount); `attempts` counts failed AI tries of the current
    attempt for the retry limit.
    """

    objects: ClassVar['Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = cast(
        'User',
        models.ForeignKey(
            settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='receipts'
        ),
    )
    user_id: UUID
    inbound_email = cast(
        'InboundEmail | None',
        models.ForeignKey(
            InboundEmail,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='receipts',
        ),
    )
    inbound_email_id: UUID | None
    source = cast(
        str,
        models.CharField(
            max_length=16, choices=ReceiptSource.choices, default=ReceiptSource.UPLOAD
        ),
    )

    # -- The stored file ----------------------------------------------------------------------
    original_filename = cast(str, models.CharField(max_length=255, blank=True, default=''))
    content_type = cast(
        str,
        models.CharField(
            max_length=100,
            blank=True,
            default='',
            help_text='Detected from the file contents (the claimed type is never trusted).',
        ),
    )
    file_key = cast(str, models.CharField(max_length=512, blank=True, default=''))
    preview_key = cast(
        str,
        models.CharField(
            max_length=512,
            blank=True,
            default='',
            help_text='A JPEG rendering, for a HEIC original browsers cannot show.',
        ),
    )
    size_bytes = cast(int, models.PositiveBigIntegerField(default=0))
    preview_size_bytes = cast(int, models.PositiveBigIntegerField(default=0))
    sha256 = cast(str, models.CharField(max_length=64, blank=True, default=''))
    upload_checksum_sha256 = cast(
        str,
        models.CharField(
            max_length=64,
            blank=True,
            default='',
            help_text='The base64 SHA-256 the client declared (and signed into the presigned '
            'PUT) for a direct upload.',
        ),
    )
    uploaded = cast(
        bool,
        models.BooleanField(
            default=True,
            help_text='False while a direct upload is in progress; the file is not billed or '
            'processed until it is completed.',
        ),
    )

    # -- State --------------------------------------------------------------------------------
    status = cast(
        str,
        models.CharField(
            max_length=24, choices=ReceiptStatus.choices, default=ReceiptStatus.RECEIVED
        ),
    )
    failure_reason = cast(
        str,
        models.CharField(max_length=24, choices=FailureReason.choices, blank=True, default=''),
    )

    # -- Extracted (and user-editable) fields -------------------------------------------------
    vendor = cast(str, models.CharField(max_length=255, blank=True, default=''))
    amount = cast(
        'Decimal | None',
        models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True),
    )
    currency = cast(str, models.CharField(max_length=3, blank=True, default=''))
    receipt_date = cast('date | None', models.DateField(null=True, blank=True))
    document_type = cast(
        str,
        models.CharField(max_length=16, choices=DocumentType.choices, blank=True, default=''),
    )
    document_number = cast(str, models.CharField(max_length=100, blank=True, default=''))
    payment_method = cast(
        str,
        models.CharField(
            max_length=100,
            blank=True,
            default='',
            help_text='Never more than the last 4 digits of a card or account number.',
        ),
    )
    category = cast(
        'ReceiptCategory | None',
        models.ForeignKey(
            ReceiptCategory,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='receipts',
        ),
    )
    category_id: int | None

    # -- Set by the user only -----------------------------------------------------------------
    notes = cast(
        str,
        models.TextField(
            blank=True,
            default='',
            validators=[MaxLengthValidator(MAX_NOTES_LENGTH)],
            help_text='Free-form notes. Never filled in or touched by the AI, nor by a re-run.',
        ),
    )

    created_at = cast(datetime, models.DateTimeField(auto_now_add=True))
    updated_at = cast(datetime, models.DateTimeField(auto_now=True))

    # -- Processing ---------------------------------------------------------------------------
    user_edited_fields = cast(
        list[str],
        models.JSONField(
            default=list,
            blank=True,
            help_text='Extracted fields the user changed by hand; a re-run never overwrites them.',
        ),
    )
    ai_result = cast(
        'dict | None',
        models.JSONField(null=True, blank=True, help_text='The last validated AI response.'),
    )
    ai_confidence = cast('float | None', models.FloatField(null=True, blank=True))
    attempt_id = cast(UUID, models.UUIDField(default=uuid.uuid4, editable=False))
    attempts = cast(int, models.PositiveSmallIntegerField(default=0))
    next_attempt_at = cast(
        'datetime | None',
        models.DateTimeField(
            null=True, blank=True, help_text='When to retry after a transient AI failure.'
        ),
    )
    processing_started_at = cast(
        'datetime | None',
        models.DateTimeField(
            null=True,
            blank=True,
            help_text='When a worker claimed it; `retry_due` recovers a receipt stuck here.',
        ),
    )
    charged_attempt_id = cast(
        'UUID | None',
        models.UUIDField(null=True, blank=True, help_text='The attempt_id that has been paid for.'),
    )
    credits_charged = cast(
        int,
        models.PositiveIntegerField(
            default=0, help_text='What the current attempt cost, i.e. what a refund returns.'
        ),
    )
    refunded_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    processed_at = cast('datetime | None', models.DateTimeField(null=True, blank=True))
    last_error = cast(str, models.CharField(max_length=255, blank=True, default=''))

    # -- Other --------------------------------------------------------------------------------
    possible_duplicate_of = cast(
        'Receipt | None',
        models.ForeignKey(
            'self',
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name='possible_duplicates',
        ),
    )
    possible_duplicate_of_id: UUID | None
    duplicate_dismissed = cast(bool, models.BooleanField(default=False))
    deleted_at = cast(
        'datetime | None',
        models.DateTimeField(null=True, blank=True, help_text='Set while the receipt is in Trash.'),
    )

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['user', 'receipt_date']),
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['sha256']),
            models.Index(fields=['status', 'next_attempt_at']),
            models.Index(fields=['deleted_at']),
        ]
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.vendor or self.original_filename or self.id} ({self.status})'

    @property
    def missing_required_fields(self) -> list[str]:
        """Which of vendor, amount and receipt date are still unset."""
        return [
            name for name in REQUIRED_FIELDS if getattr(self, name) in (None, '')
        ]  # an amount of 0.00 is a value, not a missing one
