"""
Unit tests for QRCode model.
"""

import pytest
from src.qr_code.models import (
    QRCode,
    QRCodeErrorCorrection,
    QRCodeFormat,
    QRCodeType,
    generate_short_code,
)


@pytest.mark.django_db
class TestQRCodeModel:
    """Test cases for the QRCode model."""

    def test_create_qrcode(self, user):
        """Test creating a basic QR code."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_type=QRCodeType.TEXT,
            qr_format=QRCodeFormat.PNG,
            image_file='test.png',
        )

        assert qr.content == 'https://example.com'
        assert qr.created_by == user
        assert qr.qr_type == QRCodeType.TEXT
        assert qr.qr_format == QRCodeFormat.PNG
        assert qr.scan_count == 0
        assert qr.short_code is None

    def test_qrcode_str_representation(self, user):
        """Test the string representation of QRCode."""
        qr = QRCode.objects.create(
            content='Test Content', created_by=user, qr_type=QRCodeType.TEXT, image_file='test.png'
        )

        assert 'QRCode' in str(qr)
        assert 'Test Content' in str(qr)

    def test_qrcode_default_values(self, user):
        """Test that default values are set correctly."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_type=QRCodeType.TEXT,
            image_file='test.png',
        )

        assert qr.qr_format == QRCodeFormat.PNG
        assert qr.size == 10
        assert qr.error_correction == QRCodeErrorCorrection.MEDIUM
        assert qr.border == 4
        assert qr.background_color == 'white'
        assert qr.foreground_color == 'black'
        assert qr.use_url_shortening is False

    def test_qrcode_with_url_shortening(self, user):
        """Test creating a QR code with URL shortening."""
        qr = QRCode.objects.create(
            content='https://example.com',
            original_url='https://example.com',
            use_url_shortening=True,
            created_by=user,
            qr_type=QRCodeType.TEXT,
            image_file='test.png',
        )

        assert qr.use_url_shortening is True
        assert qr.short_code is not None
        assert len(qr.short_code) == 8
        assert qr.original_url == 'https://example.com'

    def test_short_code_uniqueness(self, user):
        """Test that short codes are unique."""
        qr1 = QRCode.objects.create(
            content='https://example1.com',
            use_url_shortening=True,
            created_by=user,
            qr_type=QRCodeType.TEXT,
            image_file='test1.png',
        )

        qr2 = QRCode.objects.create(
            content='https://example2.com',
            use_url_shortening=True,
            created_by=user,
            qr_type=QRCodeType.TEXT,
            image_file='test2.png',
        )

        assert qr1.short_code != qr2.short_code

    def test_get_redirect_url(self, user):
        """Test getting the redirect URL."""
        qr = QRCode.objects.create(
            content='https://example.com',
            use_url_shortening=True,
            created_by=user,
            qr_type=QRCodeType.TEXT,
            image_file='test.png',
        )

        redirect_url = qr.get_redirect_url()

        assert redirect_url is not None
        assert '/go/' in redirect_url
        assert qr.short_code is not None and qr.short_code in redirect_url

    def test_get_redirect_url_without_shortening(self, user):
        """Test getting redirect URL when shortening is disabled."""
        qr = QRCode.objects.create(
            content='https://example.com',
            use_url_shortening=False,
            created_by=user,
            qr_type=QRCodeType.TEXT,
            image_file='test.png',
        )

        redirect_url = qr.get_redirect_url()

        assert redirect_url is None

    def test_increment_scan_count(self, user):
        """Test incrementing the scan count."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_type=QRCodeType.TEXT,
            image_file='test.png',
        )

        initial_count = qr.scan_count
        assert qr.last_scanned_at is None

        qr.increment_scan_count()

        assert qr.scan_count == initial_count + 1
        assert qr.last_scanned_at is not None

    def test_multiple_scan_increments(self, user):
        """Test multiple scan count increments."""
        qr = QRCode.objects.create(
            content='https://example.com',
            created_by=user,
            qr_type=QRCodeType.TEXT,
            image_file='test.png',
        )

        for i in range(5):
            qr.increment_scan_count()

        assert qr.scan_count == 5

    def test_qrcode_format_choices(self, user):
        """Test that all format choices work."""
        formats = [QRCodeFormat.PNG, QRCodeFormat.SVG, QRCodeFormat.PDF]

        for fmt in formats:
            qr = QRCode.objects.create(
                content=f'https://example.com/{fmt.value}',
                qr_format=fmt,
                created_by=user,
                qr_type=QRCodeType.TEXT,
                image_file=f'test.{fmt.value}',
            )
            assert qr.qr_format == fmt

    def test_error_correction_choices(self, user):
        """Test that all error correction levels work."""
        levels = [
            QRCodeErrorCorrection.LOW,
            QRCodeErrorCorrection.MEDIUM,
            QRCodeErrorCorrection.QUARTILE,
            QRCodeErrorCorrection.HIGH,
        ]

        for level in levels:
            qr = QRCode.objects.create(
                content=f'https://example.com/{level.value}',
                error_correction=level,
                created_by=user,
                qr_type=QRCodeType.TEXT,
                image_file=f'test_{level.value}.png',
            )
            assert qr.error_correction == level

    def test_qrcode_type_choices(self, user):
        """Test that all QR code type choices work."""
        types = [QRCodeType.URL, QRCodeType.TEXT]

        for qr_type in types:
            qr = QRCode.objects.create(
                content=f'content_{qr_type.value}',
                qr_type=qr_type,
                created_by=user,
                image_file=f'test_{qr_type.value}.png',
            )
            assert qr.qr_type == qr_type

    def test_qrcode_text_type(self, user):
        """Test creating a QR code with text type."""
        qr = QRCode.objects.create(
            content='Just some plain text',
            qr_type=QRCodeType.TEXT,
            created_by=user,
            image_file='test_text.png',
        )

        assert qr.qr_type == QRCodeType.TEXT
        assert qr.content == 'Just some plain text'
        assert qr.original_url is None

    def test_qrcode_url_type(self, user):
        """Test creating a QR code with URL type."""
        qr = QRCode.objects.create(
            content='https://example.com',
            qr_type=QRCodeType.TEXT,
            created_by=user,
            image_file='test_url.png',
        )

        assert qr.qr_type == QRCodeType.TEXT
        assert qr.content == 'https://example.com'


@pytest.mark.unit
def test_generate_short_code():
    """Test the short code generation function."""
    code = generate_short_code()

    assert len(code) == 8
    assert code.isalnum()


@pytest.mark.unit
def test_generate_short_code_custom_length():
    """Test generating short codes with custom length."""
    code = generate_short_code(12)

    assert len(code) == 12
    assert code.isalnum()
