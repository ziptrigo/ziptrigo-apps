import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_tools_view_redirects_non_staff_to_admin_login(client, regular_user: User):
    """A non-staff user never reaches the view at all: `admin_view()` (wrapping every custom admin
    URL, including this one) redirects straight to the admin login page, the same as it would for
    any other admin URL -- Django's own gate, not this view's own logic."""

    client.force_login(regular_user)
    url = reverse('custom_admin:admin_tools')

    response = client.get(url)

    assert response.status_code == 302
    assert response.url.startswith(reverse('custom_admin:login'))


def test_tools_view_requires_superuser_not_just_staff(client):
    """Staff-but-non-superuser users clear `admin_view()`'s staff gate and reach the view itself,
    which then denies them with its own 403 -- the tools page is superuser-only, not merely
    staff-only."""

    staff_user = UserFactory(is_staff=True, is_superuser=False)
    client.force_login(staff_user)
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

    monkeypatch.setattr('apps.core.admin_site.send_email', fake_send_email)

    response = client.post(
        url,
        {
            'recipient': 'ops@example.com',
            'send_test_email': '1',
        },
    )

    assert response.status_code == 200
    assert captured['to'] == 'ops@example.com'


def test_admin_dashboard_shows_tools_link(client, admin_user: User):
    """Dashboard should display a module linking to the admin tools."""

    client.force_login(admin_user)
    response = client.get('/admin/')

    assert response.status_code == 200
    content = response.content.decode()
    assert 'Admin Tools' in content
    assert reverse('custom_admin:admin_tools') in content
