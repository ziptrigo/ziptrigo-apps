"""Additional sanity-check tests for QR code setup and workflows.

These cover similar ground to the integration tests but are kept as
simple unit-style checks.
"""

import pytest
from django.contrib.auth import get_user_model

from src.qr_code.models import QRCode, QRCodeErrorCorrection, QRCodeFormat
from src.qr_code.services import QRCodeGenerator

User = get_user_model()


@pytest.fixture
def user(db):
    """Create a reusable test user fixture for this module."""
    user_obj, created = User.objects.get_or_create(
        username='testuser', defaults={'email': 'test@example.com'}
    )
    if created:
        user_obj.set_password('testpass123')
        user_obj.save()
    return user_obj


@pytest.mark.django_db
def test_model_creation():
    """We can create a QRCode model instance for a user."""
    user_obj, created = User.objects.get_or_create(
        username='testuser', defaults={'email': 'test@example.com'}
    )
    if created:
        user_obj.set_password('testpass123')
        user_obj.save()

    qr = QRCode.objects.create(
        content='https://example.com',
        created_by=user_obj,
        qr_format=QRCodeFormat.PNG,
        image_file='temp.png',
    )

    assert qr.pk is not None
    assert qr.created_by == user_obj


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_qr_generation(user):
    """QR code image can be generated without errors."""
    qr = QRCode.objects.create(
        content='https://example.com',
        created_by=user,
        qr_format=QRCodeFormat.PNG,
        size=10,
        error_correction=QRCodeErrorCorrection.MEDIUM,
        border=4,
        background_color='white',
        foreground_color='black',
        image_file='temp.png',
    )

    image_path = await QRCodeGenerator.generate_qr_code(qr)
    qr.image_file = image_path
    qr.save()

    assert image_path
    assert str(qr.id) in image_path


@pytest.mark.django_db
def test_url_shortening(user):
    """URL shortening produces a short_code and redirect URL when enabled."""
    qr = QRCode.objects.create(
        content='https://example.com/very-long-url',
        original_url='https://example.com/very-long-url',
        use_url_shortening=True,
        created_by=user,
        qr_format=QRCodeFormat.SVG,
        image_file='temp.svg',
    )

    assert qr.short_code
    redirect_url = qr.get_redirect_url()
    assert redirect_url
    assert qr.short_code in redirect_url


@pytest.mark.django_db
def test_scan_tracking():
    """Scan tracking increments count and updates timestamp when a QR exists."""
    qr = QRCode.objects.filter(short_code__isnull=False).first()
    if not qr:
        pytest.skip('No QR codes with short codes to test')

    initial_count = qr.scan_count
    qr.increment_scan_count()
    qr.refresh_from_db()

    assert qr.scan_count == initial_count + 1
    assert qr.last_scanned_at is not None
