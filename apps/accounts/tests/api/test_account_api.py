import pytest

from apps.accounts.models import User
from apps.accounts.tokens import CustomAccessToken

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def sent_confirmations(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        'apps.accounts.services.email_confirmation.EmailConfirmationService.send_confirmation_email',
        lambda self, user: sent.append(user.email),
    )
    return sent


def _put(client, user, payload):
    return client.put(
        '/api/account',
        payload,
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {CustomAccessToken.for_user(user)}',
    )


def test_email_change_requires_reconfirmation(client, user, sent_confirmations):
    response = _put(client, user, {'email': 'new@example.com'})

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.email == 'new@example.com'
    assert not user.email_confirmed
    assert user.email_confirmed_at is None
    assert sent_confirmations == ['new@example.com']
    assert 'confirm your new email' in response.json()['message']


def test_name_only_change_keeps_confirmation(client, user, sent_confirmations):
    response = _put(client, user, {'name': 'Renamed'})

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.name == 'Renamed'
    assert user.email_confirmed
    assert sent_confirmations == []


def test_email_in_use_is_rejected(client, user, sent_confirmations):
    User.objects.create_user(email='taken@example.com', password='password123')

    response = _put(client, user, {'email': 'taken@example.com'})

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.email == 'testuser@example.com'
    assert user.email_confirmed
