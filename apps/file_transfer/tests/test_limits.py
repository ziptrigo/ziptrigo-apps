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
