from typing import ClassVar, cast

from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models.singleton import load_singleton

_MB = 1024**2

#: Content types the sniffer (`apps.receipts.services.documents.sniff_content_type`) can produce for
#: an attachment or an upload. HEIC and HEIF share `image/heic`.
DEFAULT_ALLOWED_TYPES = 'application/pdf,image/jpeg,image/png,image/webp,image/heic'


class ReceiptsSettings(models.Model):
    """Singleton admin-editable settings for Receipts (issue #98): the per-file price, upload
    limits and the processing knobs. Always the row with `pk=1`; use `ReceiptsSettings.load()`.
    The per-GB-per-day *storage* price is not here: it is shared by every product and lives in
    `apps.billing.models.BillingSettings`.
    """

    objects: ClassVar['models.Manager']
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    price_per_file = cast(
        int,
        models.PositiveIntegerField(
            default=1,
            verbose_name='Price per file (credits)',
            help_text='Credits charged each time a file is sent to the AI for extraction (and '
            'again when the user re-runs it). 0 makes processing free.',
        ),
    )
    max_file_bytes = cast(
        int,
        models.PositiveBigIntegerField(
            default=15 * _MB,
            validators=[MinValueValidator(1)],
            verbose_name='Max file size (bytes)',
        ),
    )
    allowed_types = cast(
        str,
        models.CharField(
            max_length=255,
            default=DEFAULT_ALLOWED_TYPES,
            help_text='Comma-separated content types accepted for uploads and email '
            'attachments, as detected from the file contents (not the name or claimed type). '
            'Supported: application/pdf, image/jpeg, image/png, image/webp, image/heic.',
        ),
    )
    min_inline_image_px = cast(
        int,
        models.PositiveIntegerField(
            default=150,
            help_text='An email image counts as inline decoration (a logo, a signature) and is '
            'skipped for free when it is referenced by `cid:` and both sides are under this '
            'many pixels.',
        ),
    )
    max_files_per_user_per_day = cast(
        int,
        models.PositiveIntegerField(
            default=100,
            help_text='Receipts a user may add in a 24-hour window (uploads, API and email '
            'together). The window starts with the first receipt and lasts 24 hours; files are '
            'counted as they are added, so deleting them does not free up the cap.',
        ),
    )
    trash_days = cast(
        int,
        models.PositiveIntegerField(
            default=30,
            help_text='Days a trashed receipt is kept (and billed) before it is deleted forever.',
        ),
    )
    max_pdf_pages_to_ai = cast(
        int,
        models.PositiveIntegerField(
            default=5,
            validators=[MinValueValidator(1), MaxValueValidator(20)],
            help_text='A PDF with no text layer (a scan) is rendered to images; only this many '
            'pages are sent to the AI.',
        ),
    )
    max_ai_attempts = cast(
        int,
        models.PositiveIntegerField(
            default=3,
            validators=[MinValueValidator(1)],
            help_text='Transient AI failures (timeouts, rate limits, 5xx) are retried with a '
            'backoff; after this many the receipt fails and the charge is refunded.',
        ),
    )

    class Meta:
        verbose_name = 'Receipts settings'
        verbose_name_plural = 'Receipts settings'

    def __str__(self) -> str:
        return 'Receipts settings'

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> 'ReceiptsSettings':
        """Return the singleton row, creating it with defaults on first use."""
        return load_singleton(cls)

    def allowed_types_list(self) -> list[str]:
        return [value.strip().lower() for value in self.allowed_types.split(',') if value.strip()]

    def is_type_allowed(self, content_type: str) -> bool:
        return content_type.strip().lower() in self.allowed_types_list()
