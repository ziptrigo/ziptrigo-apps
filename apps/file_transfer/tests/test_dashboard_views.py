import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.billing.services import get_balance, spend_credits

from ..models import TransferRecipient, TransferStatus

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _active(transfer):
    transfer.status = TransferStatus.ACTIVE
    transfer.save()
    return transfer


def test_dashboard_requires_login(client):
    assert client.get(reverse('file_transfer:dashboard')).status_code == 302


def test_dashboard_lists_only_own_transfers(client, draft_transfer):
    _active(draft_transfer)
    other = UserFactory()
    client.force_login(other)

    response = client.get(reverse('file_transfer:dashboard'))

    assert response.status_code == 200
    assert draft_transfer.display_name not in response.content.decode()


def test_transfer_list_partial_filters_active_vs_ended(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    active_response = client.get(reverse('file_transfer:transfer-list'), {'filter': 'active'})
    ended_response = client.get(reverse('file_transfer:transfer-list'), {'filter': 'ended'})

    assert (
        str(draft_transfer.id) in active_response.content.decode()
        or 'transfer-row' in active_response.content.decode()
    )
    assert 'No ended transfers.' in ended_response.content.decode()


def test_disable_and_reenable_round_trip(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    disable_response = client.post(reverse('file_transfer:disable', args=[draft_transfer.id]))
    assert disable_response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DISABLED

    reenable_response = client.post(reverse('file_transfer:reenable', args=[draft_transfer.id]))
    assert reenable_response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.ACTIVE


def test_disable_other_users_transfer_is_404(client, draft_transfer):
    _active(draft_transfer)
    other = UserFactory()
    client.force_login(other)

    response = client.post(reverse('file_transfer:disable', args=[draft_transfer.id]))

    assert response.status_code == 404


def test_reenable_without_credits_returns_422(client, draft_transfer):
    draft_transfer.status = TransferStatus.SUSPENDED
    draft_transfer.suspended_at = timezone.now()
    draft_transfer.save()
    spend_credits(draft_transfer.owner, get_balance(draft_transfer.owner), source='test')
    client.force_login(draft_transfer.owner)

    response = client.post(reverse('file_transfer:reenable', args=[draft_transfer.id]))

    assert response.status_code == 422
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.SUSPENDED


def test_delete_now(client, draft_transfer, uploaded_file, fake_storage):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    response = client.post(reverse('file_transfer:delete', args=[draft_transfer.id]))

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.status == TransferStatus.DELETED
    assert uploaded_file.storage_key not in fake_storage.objects


def test_update_settings_success(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:update-settings', args=[draft_transfer.id]),
        data={'expiry_choice': '5', 'max_downloads': '3', 'password': ''},
    )

    assert response.status_code == 200
    draft_transfer.refresh_from_db()
    assert draft_transfer.max_downloads == 3
    assert draft_transfer.expires_at is not None


def test_update_settings_validation_error(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    response = client.post(
        reverse('file_transfer:update-settings', args=[draft_transfer.id]),
        data={'expiry_choice': 'custom', 'expiry_date': '', 'max_downloads': ''},
    )

    assert response.status_code == 422


def test_add_recipients_and_resend(client, draft_transfer):
    _active(draft_transfer)
    client.force_login(draft_transfer.owner)

    add_response = client.post(
        reverse('file_transfer:add-recipients', args=[draft_transfer.id]),
        data={'recipients': 'friend@example.com'},
    )
    assert add_response.status_code == 200
    recipient = TransferRecipient.objects.get(transfer=draft_transfer, email='friend@example.com')

    resend_response = client.post(
        reverse('file_transfer:resend-recipient', args=[draft_transfer.id, recipient.id])
    )
    assert resend_response.status_code == 200
    recipient.refresh_from_db()
    assert recipient.last_sent_at is not None
