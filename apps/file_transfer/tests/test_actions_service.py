from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.services import InsufficientCreditsError, get_balance, spend_credits

from ..models import TransferRecipient, TransferStatus
from ..services import actions

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _active(transfer, **overrides):
    transfer.status = TransferStatus.ACTIVE
    for key, value in overrides.items():
        setattr(transfer, key, value)
    transfer.save()
    return transfer


def test_disable_requires_active_status(draft_transfer):
    with pytest.raises(ValidationError):
        actions.disable_transfer(draft_transfer)


def test_disable_then_reenable_round_trip(draft_transfer):
    _active(draft_transfer)
    actions.disable_transfer(draft_transfer)
    assert draft_transfer.status == TransferStatus.DISABLED

    actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_reenable_suspended_requires_credit(draft_transfer):
    _active(draft_transfer, status=TransferStatus.SUSPENDED, suspended_at=timezone.now())
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')

    with pytest.raises(InsufficientCreditsError):
        actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.SUSPENDED


def test_reenable_suspended_succeeds_with_credit(draft_transfer):
    _active(draft_transfer, status=TransferStatus.SUSPENDED, suspended_at=timezone.now())
    actions.reenable_transfer_action(draft_transfer)
    assert draft_transfer.status == TransferStatus.ACTIVE
    assert draft_transfer.suspended_at is None


def test_reenable_rejects_active_transfer(draft_transfer):
    _active(draft_transfer)
    with pytest.raises(ValidationError):
        actions.reenable_transfer_action(draft_transfer)


def test_delete_transfer_now_is_terminal_and_deletes_files(
    draft_transfer, uploaded_file, fake_storage
):
    _active(draft_transfer)
    actions.delete_transfer_now(draft_transfer)
    assert draft_transfer.status == TransferStatus.DELETED
    assert draft_transfer.deleted_at is not None
    assert uploaded_file.storage_key not in fake_storage.objects


def test_delete_transfer_now_rejects_already_ended(draft_transfer):
    _active(draft_transfer, status=TransferStatus.DELETED, deleted_at=timezone.now())
    with pytest.raises(ValidationError):
        actions.delete_transfer_now(draft_transfer)


def test_set_expiry_extends_and_removes(draft_transfer):
    _active(draft_transfer)
    actions.set_expiry(draft_transfer, '5')
    assert draft_transfer.expires_at is not None

    actions.set_expiry(draft_transfer, 'none')
    assert draft_transfer.expires_at is None


def test_set_expiry_custom_requires_future_date(draft_transfer):
    _active(draft_transfer)
    with pytest.raises(ValidationError):
        actions.set_expiry(draft_transfer, 'custom', timezone.now() - timedelta(days=1))

    future = timezone.now() + timedelta(days=2)
    actions.set_expiry(draft_transfer, 'custom', future)
    assert draft_transfer.expires_at == future


def test_set_max_downloads_validates_minimum(draft_transfer):
    _active(draft_transfer)
    with pytest.raises(ValidationError):
        actions.set_max_downloads(draft_transfer, 0)

    actions.set_max_downloads(draft_transfer, 3)
    assert draft_transfer.max_downloads == 3

    actions.set_max_downloads(draft_transfer, None)
    assert draft_transfer.max_downloads is None


def test_set_and_remove_password(draft_transfer):
    _active(draft_transfer)
    actions.set_password(draft_transfer, 'sekret')
    assert draft_transfer.password_hash

    actions.remove_password(draft_transfer)
    assert draft_transfer.password_hash == ''


def test_add_recipients_dedupes_against_existing(draft_transfer):
    _active(draft_transfer)
    TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')

    created = actions.add_recipients(draft_transfer, ['a@example.com', 'b@example.com'])

    assert [r.email for r in created] == ['b@example.com']
    assert TransferRecipient.objects.filter(transfer=draft_transfer).count() == 2


def test_add_recipients_enforces_combined_max(draft_transfer, ft_settings):
    _active(draft_transfer)
    ft_settings.logged_in_max_recipients = 1
    ft_settings.save()
    TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')

    with pytest.raises(ValidationError):
        actions.add_recipients(draft_transfer, ['b@example.com'])


def test_resend_recipient_email_updates_last_sent_at(draft_transfer):
    _active(draft_transfer)
    recipient = TransferRecipient.objects.create(transfer=draft_transfer, email='a@example.com')
    assert recipient.last_sent_at is None

    actions.resend_recipient_email(recipient)

    recipient.refresh_from_db()
    assert recipient.last_sent_at is not None


def test_actions_reject_ended_transfer(draft_transfer):
    _active(draft_transfer, status=TransferStatus.EXPIRED)
    with pytest.raises(ValidationError):
        actions.set_max_downloads(draft_transfer, 1)
    with pytest.raises(ValidationError):
        actions.add_recipients(draft_transfer, ['a@example.com'])
