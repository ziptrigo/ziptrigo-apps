"""`apps.qr_code.services.management`: rules shared by the web views and the API."""

import pytest
from django.core.exceptions import ValidationError

from apps.qr_code import services
from apps.qr_code.models import QRCode, QRCodeType

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def test_short_code_taken_between_check_and_save_gets_a_new_code(
    user, qr_code_with_shortening, monkeypatch
):
    # Simulate a race: the availability check passes, but the code is taken by the time we save.
    taken = qr_code_with_shortening.short_code
    monkeypatch.setattr(services.management, 'is_short_code_available', lambda code: True)

    qrcode = services.create_qrcode(
        user,
        name='Race',
        qr_type=QRCodeType.URL,
        qr_format='png',
        content='https://example.com',
        use_url_shortening=True,
        short_code=taken,
    )

    assert qrcode.short_code
    assert qrcode.short_code != taken
    assert QRCode.objects.filter(short_code=qrcode.short_code).count() == 1


@pytest.mark.parametrize(
    ('qr_type', 'content'),
    [
        (QRCodeType.URL, 'not a url'),
        (QRCodeType.URL, 'javascript:alert(1)'),
        (QRCodeType.TEXT, ''),
        (QRCodeType.TEXT, 'x' * 1001),
    ],
)
def test_validate_content_rejects(qr_type, content):
    with pytest.raises(ValidationError):
        services.validate_content(qr_type, content)


@pytest.mark.parametrize(
    ('qr_type', 'content'),
    [(QRCodeType.URL, 'https://example.com/a?b=c'), (QRCodeType.TEXT, 'anything at all')],
)
def test_validate_content_accepts(qr_type, content):
    services.validate_content(qr_type, content)


def test_new_short_code_is_available(qr_code_with_shortening):
    assert services.is_short_code_available(services.new_short_code())
