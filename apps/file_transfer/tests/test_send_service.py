from datetime import datetime

import pytest
from django.core.exceptions import ValidationError

from apps.billing.services import InsufficientCreditsError, get_balance

from ..models import TransferRecipient, TransferStatus
from ..services.send import SendOptions, finalize_send

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _options(
    *,
    recipients: list[str] | None = None,
    message: str = 'hello',
    expiry_choice: str = 'none',
    expiry_date: datetime | None = None,
    max_downloads: int | None = None,
    password: str = '',
    notify_on_download: bool = True,
) -> SendOptions:
    return SendOptions(
        recipients=recipients if recipients is not None else ['a@example.com'],
        message=message,
        expiry_choice=expiry_choice,
        expiry_date=expiry_date,
        max_downloads=max_downloads,
        password=password,
        notify_on_download=notify_on_download,
    )


def test_finalize_send_activates_transfer_and_charges_no_credits_yet(
    draft_transfer, uploaded_file, django_capture_on_commit_callbacks
):
    balance_before = get_balance(draft_transfer.owner)

    with django_capture_on_commit_callbacks(execute=True):
        finalize_send(draft_transfer, _options())

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.completed_at is not None
    assert draft_transfer.last_billed_at is not None
    assert draft_transfer.size_bytes == uploaded_file.size
    assert TransferRecipient.objects.filter(transfer=draft_transfer, email='a@example.com').exists()
    # Sending itself never spends credits -- only the daily metering job does.
    assert get_balance(draft_transfer.owner) == balance_before


def test_finalize_send_requires_at_least_one_uploaded_file(draft_transfer):
    with pytest.raises(ValidationError):
        finalize_send(draft_transfer, _options())


def test_finalize_send_requires_a_recipient(draft_transfer, uploaded_file):
    with pytest.raises(ValidationError):
        finalize_send(draft_transfer, _options(recipients=[]))


def test_finalize_send_rejects_a_non_draft_transfer(draft_transfer, uploaded_file):
    draft_transfer.status = TransferStatus.ACTIVE
    draft_transfer.save()
    with pytest.raises(ValidationError):
        finalize_send(draft_transfer, _options())


def test_finalize_send_requires_minimum_balance(draft_transfer, uploaded_file):
    from apps.billing.services import spend_credits

    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')
    assert get_balance(draft_transfer.owner) == 0

    with pytest.raises(InsufficientCreditsError):
        finalize_send(draft_transfer, _options())

    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DRAFT


def test_finalize_send_sets_password_hash_when_given(draft_transfer, uploaded_file):
    finalize_send(draft_transfer, _options(password='sekret'))
    draft_transfer.refresh_from_db()
    assert draft_transfer.password_hash
    assert draft_transfer.password_hash != 'sekret'


def test_finalize_send_dedupes_recipients(draft_transfer, uploaded_file):
    finalize_send(draft_transfer, _options(recipients=['a@example.com', 'A@example.com']))
    assert TransferRecipient.objects.filter(transfer=draft_transfer).count() == 1
