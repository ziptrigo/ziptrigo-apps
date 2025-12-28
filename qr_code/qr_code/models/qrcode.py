import random
import string
import uuid

from asgiref.sync import sync_to_async
from django.db import models

from .user import User


def generate_short_code(length: int = 8) -> str:
    """Generate a random short code for URL shortening."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


class QRCodeFormat(models.TextChoices):
    """Enum for QR code output formats."""

    PNG = 'png', 'PNG'
    SVG = 'svg', 'SVG'
    PDF = 'pdf', 'PDF'


class QRCodeErrorCorrection(models.TextChoices):
    """Enum for QR code error correction levels."""

    LOW = 'L', 'Low (~7%)'
    MEDIUM = 'M', 'Medium (~15%)'
    QUARTILE = 'Q', 'Quartile (~25%)'
    HIGH = 'H', 'High (~30%)'


class QRCodeType(models.TextChoices):
    """Enum for QR code types."""

    URL = 'url', 'URL'
    TEXT = 'text', 'Text'


class QRCode(models.Model):
    """Model to store QR code data and settings."""

    # Primary identification
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='qrcodes')

    # QR Code content and settings
    name = models.CharField(
        max_length=255, default='Untitled QR Code', help_text='Name of the QR code'
    )
    qr_type = models.CharField(
        max_length=10, choices=QRCodeType.choices, help_text='Type of QR code content'
    )
    content = models.TextField(help_text='The actual content encoded in the QR code')
    original_url = models.URLField(
        max_length=2000,
        null=True,
        blank=True,
        help_text='Original URL if content is a shortened URL',
    )
    use_url_shortening = models.BooleanField(
        default=False, help_text='Whether to use URL shortening'
    )
    short_code = models.CharField(
        max_length=16, unique=True, null=True, blank=True, help_text='Short code for URL shortening'
    )

    # QR Code customization
    qr_format = models.CharField(
        max_length=10, choices=QRCodeFormat.choices, default=QRCodeFormat.PNG
    )
    size = models.IntegerField(default=10, help_text='Scale factor for QR code size')
    error_correction = models.CharField(
        max_length=1, choices=QRCodeErrorCorrection.choices, default=QRCodeErrorCorrection.MEDIUM
    )
    border = models.IntegerField(default=4, help_text='Border size (quiet zone)')

    # Colors
    background_color = models.CharField(
        max_length=20, default='white', help_text='Background color (hex, name, or "transparent")'
    )
    foreground_color = models.CharField(
        max_length=20, default='black', help_text='Foreground (data) color'
    )

    # File storage
    image_file = models.CharField(max_length=255, help_text='Path to generated image file')

    # Analytics
    scan_count = models.IntegerField(default=0, help_text='Number of times QR code was scanned')
    last_scanned_at = models.DateTimeField(null=True, blank=True)

    # Soft delete
    deleted_at = models.DateTimeField(
        null=True, blank=True, help_text='Timestamp when QR code was soft-deleted'
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['short_code']),
            models.Index(fields=['created_by', '-created_at']),
            models.Index(fields=['deleted_at']),
        ]
        verbose_name = 'QR Code'
        verbose_name_plural = 'QR Codes'

    def __str__(self) -> str:
        return f'QRCode {self.id} - {self.content[:50]}'

    def save(self, *args, **kwargs):
        # Generate short code if URL shortening is enabled and code doesn't exist
        if self.use_url_shortening and not self.short_code:
            self.short_code = generate_short_code()
            # Ensure uniqueness
            while QRCode.objects.filter(short_code=self.short_code).exists():
                self.short_code = generate_short_code()

        super().save(*args, **kwargs)

    def get_redirect_url(self) -> str | None:
        """Get the full redirect URL for this QR code."""
        from django.conf import settings

        if self.short_code:
            return f'{settings.BASE_URL}{settings.QR_CODE_REDIRECT_PATH}{self.short_code}'
        return None

    def increment_scan_count(self):
        """Increment the scan count and update last scanned timestamp."""
        from django.utils import timezone

        self.scan_count += 1
        self.last_scanned_at = timezone.now()
        self.save(update_fields=['scan_count', 'last_scanned_at'])

    async def aincrement_scan_count(self):
        """Async version: Increment the scan count and update last scanned timestamp."""
        await sync_to_async(self.increment_scan_count)()

    def soft_delete(self):
        """Mark this QR code as deleted without removing it from the database."""
        from django.utils import timezone

        if not self.deleted_at:
            self.deleted_at = timezone.now()
            self.save(update_fields=['deleted_at'])

    async def asoft_delete(self):
        """Async version: Mark this QR code as deleted without removing it from the database."""
        await sync_to_async(self.soft_delete)()
