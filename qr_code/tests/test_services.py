"""
Unit tests for QR code generation services.
"""

from pathlib import Path

import pytest
from django.conf import settings
from src.qr_code.models import QRCode, QRCodeErrorCorrection, QRCodeFormat
from src.qr_code.services import QRCodeGenerator


@pytest.mark.django_db
class TestQRCodeGenerator:
    """Test cases for the QRCodeGenerator service."""

    @pytest.mark.asyncio
    async def test_generate_png_qrcode(self, user, tmp_path):
        """Test generating a PNG QR code."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_format=QRCodeFormat.PNG,
            image_file='temp.png',
        )

        image_path = await QRCodeGenerator.generate_qr_code(qr)

        assert image_path is not None
        assert image_path.endswith('.png')
        assert str(qr.id) in image_path

        # Verify file exists
        full_path = Path(settings.MEDIA_ROOT) / image_path
        assert full_path.exists()

    @pytest.mark.asyncio
    async def test_generate_svg_qrcode(self, user):
        """Test generating an SVG QR code."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_format=QRCodeFormat.SVG,
            image_file='temp.svg',
        )

        image_path = await QRCodeGenerator.generate_qr_code(qr)

        assert image_path is not None
        assert image_path.endswith('.svg')

        # Verify file exists
        full_path = Path(settings.MEDIA_ROOT) / image_path
        assert full_path.exists()

    @pytest.mark.asyncio
    async def test_generate_pdf_qrcode(self, user):
        """Test generating a PDF QR code."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_format=QRCodeFormat.PDF,
            image_file='temp.pdf',
        )

        image_path = await QRCodeGenerator.generate_qr_code(qr)

        assert image_path is not None
        assert image_path.endswith('.pdf')

        # Verify file exists
        full_path = Path(settings.MEDIA_ROOT) / image_path
        assert full_path.exists()

    @pytest.mark.asyncio
    async def test_generate_with_custom_colors(self, user):
        """Test generating QR code with custom colors."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_format=QRCodeFormat.PNG,
            background_color='#FFFFFF',
            foreground_color='#000000',
            image_file='temp.png',
        )

        image_path = await QRCodeGenerator.generate_qr_code(qr)

        assert image_path is not None
        full_path = Path(settings.MEDIA_ROOT) / image_path
        assert full_path.exists()

    @pytest.mark.asyncio
    async def test_generate_with_transparent_background(self, user):
        """Test generating QR code with transparent background."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_format=QRCodeFormat.PNG,
            background_color='transparent',
            foreground_color='black',
            image_file='temp.png',
        )

        image_path = await QRCodeGenerator.generate_qr_code(qr)

        assert image_path is not None
        full_path = Path(settings.MEDIA_ROOT) / image_path
        assert full_path.exists()

    @pytest.mark.asyncio
    async def test_generate_with_custom_size(self, user):
        """Test generating QR code with custom size."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_format=QRCodeFormat.PNG,
            size=15,
            image_file='temp.png',
        )

        image_path = await QRCodeGenerator.generate_qr_code(qr)

        assert image_path is not None
        full_path = Path(settings.MEDIA_ROOT) / image_path
        assert full_path.exists()

    @pytest.mark.asyncio
    async def test_generate_with_custom_border(self, user):
        """Test generating QR code with custom border."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_format=QRCodeFormat.PNG,
            border=10,
            image_file='temp.png',
        )

        image_path = await QRCodeGenerator.generate_qr_code(qr)

        assert image_path is not None
        full_path = Path(settings.MEDIA_ROOT) / image_path
        assert full_path.exists()

    @pytest.mark.asyncio
    async def test_generate_with_all_error_correction_levels(self, user):
        """Test generating QR codes with different error correction levels."""
        levels = [
            QRCodeErrorCorrection.LOW,
            QRCodeErrorCorrection.MEDIUM,
            QRCodeErrorCorrection.QUARTILE,
            QRCodeErrorCorrection.HIGH,
        ]

        for level in levels:
            qr = QRCode.objects.create(
                content=f'https://example.com/{level.value}',
                created_by=user,
                qr_format=QRCodeFormat.PNG,
                error_correction=level,
                image_file=f'temp_{level.value}.png',
            )

            image_path = await QRCodeGenerator.generate_qr_code(qr)

            assert image_path is not None
            full_path = Path(settings.MEDIA_ROOT) / image_path
            assert full_path.exists()

    @pytest.mark.unit
    def test_parse_color_transparent(self):
        """Test parsing transparent color."""
        result = QRCodeGenerator._parse_color('transparent')
        assert result is None

    @pytest.mark.unit
    def test_parse_color_named(self):
        """Test parsing named colors."""
        result = QRCodeGenerator._parse_color('white')
        assert result == 'white'

    @pytest.mark.unit
    def test_parse_color_hex(self):
        """Test parsing hex colors."""
        result = QRCodeGenerator._parse_color('#FF0000')
        assert result == '#FF0000'

    @pytest.mark.unit
    def test_get_file_url(self):
        """Test getting file URL."""
        image_file = 'qrcodes/test.png'
        url = QRCodeGenerator.get_file_url(image_file)

        assert url == f'/media/{image_file}'
