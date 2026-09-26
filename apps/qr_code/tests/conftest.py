"""
Pytest configuration and fixtures for the qr_code app. `user` comes from the root `conftest.py`.
"""

import pytest

from apps.qr_code.models import QRCode, QRCodeErrorCorrection, QRCodeFormat, QRCodeType


@pytest.fixture
def qr_code(user):
    """Create a test QR code."""
    return QRCode.objects.create(
        content='https://example.com',
        created_by=user,
        qr_type=QRCodeType.TEXT,
        qr_format=QRCodeFormat.PNG,
        size=10,
        error_correction=QRCodeErrorCorrection.MEDIUM,
        border=4,
        background_color='white',
        foreground_color='black',
        image_file='test.png',
    )


@pytest.fixture
def qr_code_with_shortening(user):
    """Create a test QR code with URL shortening."""
    return QRCode.objects.create(
        content='https://example.com/long-url',
        original_url='https://example.com/long-url',
        use_url_shortening=True,
        created_by=user,
        qr_type=QRCodeType.TEXT,
        qr_format=QRCodeFormat.PNG,
        image_file='test_short.png',
    )
