import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.admin_site import is_sensitive_env_name, mask_secret, mask_url_password

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


@pytest.mark.parametrize(
    'key',
    [
        'JWT_SECRET',
        'SECRET_KEY',
        'FILE_TRANSFER_AWS_ACCESS_KEY_ID',
        'RECEIPTS_AWS_SECRET_ACCESS_KEY',
        'OPENAI_API_KEY',
        'GOOGLE_CLIENT_SECRET',
        'TURNSTILE_SECRET_KEY',
        'AWS_SESSION_TOKEN',
        'SOME_PRIVATE_THING',
    ],
)
def test_secret_names_are_sensitive(key: str):
    assert is_sensitive_env_name(key)


@pytest.mark.parametrize(
    'key',
    [
        'AWS_REGION',
        'AWS_SES_SENDER',
        'EMAIL_CONFIRMATION_TOKEN_TTL_HOURS',
        'PASSWORD_RESET_TOKEN_TTL_HOURS',
        'JWT_EXP_DELTA_SECONDS',
        'JWT_ALGORITHM',
        'KEYBOARD_LAYOUT',
        'BASE_URL',
    ],
)
def test_harmless_names_are_not_sensitive(key: str):
    assert not is_sensitive_env_name(key)


def test_mask_secret_shows_at_most_the_last_four_characters_of_a_long_value():
    assert mask_secret('short') == '********'
    assert mask_secret('fifteen-chars!!') == '********'
    assert mask_secret('0123456789abcdefWXYZ') == '********WXYZ'


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('postgres://app:s3cret@db:5432/app', 'postgres://app:********@db:5432/app'),
        ('redis://:s3cret@cache:6379/0', 'redis://:********@cache:6379/0'),
        ('redis://token-only@cache:6379/0', 'redis://********@cache:6379/0'),
        # An unencoded `@` or `/` in the password over-masks instead of leaking its tail.
        ('postgres://app:pa@ss/w@db/app', 'postgres://app:********@db/app'),
        ('https://app.ziptrigo.com/go/', 'https://app.ziptrigo.com/go/'),
        ('local', 'local'),
    ],
)
def test_mask_url_password(value: str, expected: str):
    assert mask_url_password(value) == expected


def test_show_environment_masks_credentials_in_urls_and_access_key_ids(
    client, admin_user: User, monkeypatch
):
    client.force_login(admin_user)
    monkeypatch.setenv('DATABASE_URL', 'postgres://ziptrigo:db-password-123@db:5432/ziptrigo')
    monkeypatch.setenv('FILE_TRANSFER_AWS_ACCESS_KEY_ID', 'AKIAEXAMPLEEXAMPLE12')
    monkeypatch.setenv('PASSWORD_RESET_TOKEN_TTL_HOURS', '4')

    response = client.post(reverse('custom_admin:admin_tools'), {'show_environment': '1'})

    env = dict(response.context['environment_variables'] or [])
    assert env['DATABASE_URL'] == 'postgres://ziptrigo:********@db:5432/ziptrigo'
    assert env['FILE_TRANSFER_AWS_ACCESS_KEY_ID'] == '********LE12'
    assert env['PASSWORD_RESET_TOKEN_TTL_HOURS'] == '4'
    assert 'db-password-123' not in response.content.decode()
    assert 'AKIAEXAMPLEEXAMPLE12' not in response.content.decode()


def test_the_admin_tools_link_permission_exists_nowhere(settings):
    """The jazzmin menu shows Admin Tools only to holders of `core.superuser_only`, a permission
    that must never exist: only superusers, who have every permission, may pass. Creating it, or
    a group granting it, would show the link to staff the page then refuses."""
    from django.contrib.auth.models import Permission

    [link] = [
        item for item in settings.JAZZMIN_SETTINGS['topmenu_links'] if item['name'] == 'Admin Tools'
    ]
    assert link['permissions'] == ['core.superuser_only']
    assert not Permission.objects.filter(
        content_type__app_label='core', codename='superuser_only'
    ).exists()


def test_staff_holding_every_real_permission_still_does_not_see_the_link(client):
    from django.contrib.auth.models import Permission

    staff = UserFactory(is_staff=True, is_superuser=False)
    # `ty` sees `user_permissions` as the declared `ManyToManyField`, not the related manager.
    staff.user_permissions.set(Permission.objects.all())  # ty: ignore[unresolved-attribute]
    client.force_login(staff)

    html = client.get(reverse('custom_admin:index')).content.decode()

    assert reverse('custom_admin:admin_tools') not in html
