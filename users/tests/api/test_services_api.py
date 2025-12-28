import pytest
from src.users.models import Service

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_services_requires_admin(api_client, regular_user):
    api_client.force_authenticate(user=regular_user)

    response = api_client.get('/api/services')

    assert response.status_code == 403


def test_admin_can_create_service_and_client_credentials_are_generated(
    api_client, admin_user, monkeypatch
):
    api_client.force_authenticate(user=admin_user)

    calls: list[int] = []

    def fake_token_urlsafe(n: int) -> str:
        calls.append(n)
        return f'token-{n}'

    monkeypatch.setattr('src.users.routers.services.secrets.token_urlsafe', fake_token_urlsafe)

    response = api_client.post(
        '/api/services',
        {'name': 'My Service', 'description': 'Test', 'status': 'ACTIVE'},
        format='json',
    )

    assert response.status_code == 201
    assert response.data['name'] == 'My Service'
    assert response.data['client_id'] == 'token-32'

    service = Service.objects.get(id=response.data['id'])
    assert service.client_id == 'token-32'
    assert service.client_secret == 'token-64'
    assert calls == [32, 64]
