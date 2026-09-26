from pathlib import Path

import segno
from asgiref.sync import sync_to_async
from django.conf import settings

from ..models import QRCode, QRCodeFormat


class QRCodeGenerator:
    """Service class for generating QR codes using segno."""

    @staticmethod
    async def generate_qr_code(qr_code_instance: QRCode) -> str:
        """Generate a QR code image file based on the QRCode model instance."""
        # Create QR code with segno (CPU-bound, run in executor)
        qr = await sync_to_async(segno.make)(
            qr_code_instance.content,
            error=qr_code_instance.error_correction,
            micro=False,
        )

        # Prepare the file path
        media_qrcodes = Path(settings.MEDIA_ROOT) / 'qrcodes'
        await sync_to_async(media_qrcodes.mkdir)(parents=True, exist_ok=True)

        file_name = f'{qr_code_instance.id}.{qr_code_instance.qr_format}'
        file_path = media_qrcodes / file_name

        # Prepare color values
        bg_color = QRCodeGenerator._parse_color(qr_code_instance.background_color)
        fg_color = QRCodeGenerator._parse_color(qr_code_instance.foreground_color)

        # Generate based on format (I/O-bound, run in executor)
        if qr_code_instance.qr_format == QRCodeFormat.SVG:
            await sync_to_async(qr.save)(
                str(file_path),
                kind='svg',
                scale=qr_code_instance.size,
                border=qr_code_instance.border,
                dark=fg_color,
                light=bg_color,
            )
        else:  # png, pdf, or other formats
            await sync_to_async(qr.save)(
                str(file_path),
                kind=qr_code_instance.qr_format,
                scale=qr_code_instance.size,
                border=qr_code_instance.border,
                dark=fg_color,
                light=bg_color,
            )

        # Return relative path for storage
        return f'qrcodes/{file_name}'

    @staticmethod
    def _parse_color(color_value: str) -> str | None:
        """Parse color value to format accepted by segno."""
        if color_value.lower() == 'transparent':
            return None
        return color_value

    @staticmethod
    def get_file_url(image_file: str) -> str:
        """Get the full URL for accessing the QR code image."""
        return f'{settings.MEDIA_URL}{image_file}'
