import base64

import pytest
from django.core.exceptions import ValidationError

from ..models import TransferFile
from ..services import limits

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_validate_message_ok_within_limit():
    limits.validate_message('x' * limits.MAX_MESSAGE_LENGTH)


def test_validate_message_too_long_raises():
    with pytest.raises(ValidationError):
        limits.validate_message('x' * (limits.MAX_MESSAGE_LENGTH + 1))


def test_validate_new_file_rejects_empty_file(draft_transfer, ft_settings):
    with pytest.raises(ValidationError):
        limits.validate_new_file(draft_transfer, 0, ft_settings)


def test_validate_new_file_rejects_over_max_file_size(draft_transfer, ft_settings):
    ft_settings.logged_in_max_file_size_bytes = 100
    with pytest.raises(ValidationError):
        limits.validate_new_file(draft_transfer, 101, ft_settings)
    limits.validate_new_file(draft_transfer, 100, ft_settings)  # exactly at the limit is fine


def test_validate_new_file_rejects_over_max_file_count(draft_transfer, ft_settings):
    ft_settings.logged_in_max_files = 1
    TransferFile.objects.create(transfer=draft_transfer, name='a', size=1, storage_key='k')
    with pytest.raises(ValidationError):
        limits.validate_new_file(draft_transfer, 1, ft_settings)


def test_validate_new_file_rejects_over_max_total_size(draft_transfer, ft_settings):
    ft_settings.logged_in_max_total_size_bytes = 100
    TransferFile.objects.create(transfer=draft_transfer, name='a', size=60, storage_key='k')
    with pytest.raises(ValidationError):
        limits.validate_new_file(draft_transfer, 41, ft_settings)
    limits.validate_new_file(draft_transfer, 40, ft_settings)  # exactly at the limit is fine


def test_validate_recipients_dedupes_case_insensitively(ft_settings):
    result = limits.validate_recipients(
        ['A@example.com', 'a@example.com', 'b@example.com'], ft_settings
    )
    assert result == ['A@example.com', 'b@example.com']


def test_validate_recipients_enforces_max(ft_settings):
    ft_settings.logged_in_max_recipients = 1
    with pytest.raises(ValidationError):
        limits.validate_recipients(['a@example.com', 'b@example.com'], ft_settings)


def test_validate_has_files_requires_an_uploaded_file(draft_transfer):
    with pytest.raises(ValidationError):
        limits.validate_has_files(draft_transfer)

    TransferFile.objects.create(
        transfer=draft_transfer, name='a', size=1, storage_key='k', uploaded=False
    )
    with pytest.raises(ValidationError):
        limits.validate_has_files(draft_transfer)

    TransferFile.objects.create(
        transfer=draft_transfer, name='b', size=1, storage_key='k2', uploaded=True
    )
    limits.validate_has_files(draft_transfer)


def test_validate_filename_strips_control_characters():
    assert limits.validate_filename('re\r\nport\x00.pdf') == 'report.pdf'


def test_validate_filename_keeps_non_ascii():
    assert limits.validate_filename('résumé.pdf') == 'résumé.pdf'


def test_validate_filename_rejects_empty_after_stripping():
    with pytest.raises(ValidationError):
        limits.validate_filename('\x00\x00')
    with pytest.raises(ValidationError):
        limits.validate_filename('   ')


def test_validate_checksum_sha256_accepts_well_formed_digest():
    value = base64.b64encode(b'\x00' * 32).decode()
    assert limits.validate_checksum_sha256(value) == value


def test_validate_checksum_sha256_rejects_wrong_length():
    with pytest.raises(ValidationError):
        limits.validate_checksum_sha256(base64.b64encode(b'\x00' * 16).decode())


def test_validate_checksum_sha256_rejects_non_base64():
    with pytest.raises(ValidationError):
        limits.validate_checksum_sha256('not base64!!')
