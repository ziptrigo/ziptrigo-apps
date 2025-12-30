import pytest
from django.urls import reverse

from src.users.models import CreditTransaction, User
from tests.factories import UserFactory

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_tools_view_requires_superuser(client, regular_user: User):
    """Non-superusers should be denied access to the tools view."""

    client.force_login(regular_user)
    url = reverse('custom_admin:admin_tools')

    response = client.get(url)

    assert response.status_code == 403
    assert 'You do not have permission' in response.content.decode()


def test_show_environment_masks_sensitive_values(client, admin_user: User, monkeypatch):
    """Environment variables should be masked except for whitelisted keys."""

    client.force_login(admin_user)
    url = reverse('custom_admin:admin_tools')

    monkeypatch.setenv('MY_SECRET_TOKEN', 'super-secret-value')

    response = client.post(url, {'show_environment': '1'})

    assert response.status_code == 200
    env_vars = response.context['environment_variables']
    env_dict = dict(env_vars or [])
    masked_value = env_dict.get('MY_SECRET_TOKEN')
    assert masked_value is not None
    assert masked_value != 'super-secret-value'
    assert '*' in masked_value


def test_send_test_email_invokes_service(client, admin_user: User, monkeypatch):
    """Sending a test email should call the email service."""

    client.force_login(admin_user)
    url = reverse('custom_admin:admin_tools')
    captured: dict[str, str] = {}

    def fake_send_email(**kwargs):
        captured['to'] = kwargs['to']
        return 1, 0

    monkeypatch.setattr('src.users.admin.send_email', fake_send_email)

    response = client.post(
        url,
        {
            'recipient': 'ops@example.com',
            'send_test_email': '1',
        },
    )

    assert response.status_code == 200
    assert captured['to'] == 'ops@example.com'


def test_adjust_credits_creates_transaction(client, admin_user: User):
    """Adjusting credits should update the ledger and balance."""

    client.force_login(admin_user)
    url = reverse('custom_admin:admin_tools')
    target = UserFactory(email='target@example.com', credits=10)

    response = client.post(
        url,
        {
            'user_email': target.email,
            'direction': 'add',
            'amount': 25,
            'description': 'Manual top-up',
            'adjust_credits': '1',
        },
    )

    assert response.status_code == 200
    target.refresh_from_db()
    assert target.credits == 35

    transaction = CreditTransaction.objects.get(user=target)
    assert transaction.amount == 25
    assert transaction.description == 'Manual top-up'
