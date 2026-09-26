import pytest
from django.urls import reverse

from apps.accounts.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_add_user_hashes_password(client, admin_user):
    client.force_login(admin_user)

    response = client.post(
        reverse('custom_admin:accounts_user_add'),
        {
            'email': 'new@example.com',
            'name': 'New User',
            'usable_password': 'true',
            'password1': 'S3cure-pass-42',
            'password2': 'S3cure-pass-42',
        },
    )

    assert response.status_code == 302
    user = User.objects.get(email='new@example.com')
    assert user.password != 'S3cure-pass-42'
    assert user.check_password('S3cure-pass-42')


def test_change_form_shows_password_hash_read_only(client, admin_user, regular_user):
    client.force_login(admin_user)

    response = client.get(reverse('custom_admin:accounts_user_change', args=[regular_user.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="password"' not in content
    # The widget links to the change-password form, relative to the change page.
    assert 'href="../password/"' in content
    password_url = reverse('custom_admin:auth_user_password_change', args=[regular_user.pk])
    assert client.get(password_url).status_code == 200


def test_change_form_saves_without_touching_password(client, admin_user, regular_user):
    client.force_login(admin_user)
    old_hash = regular_user.password

    response = client.post(
        reverse('custom_admin:accounts_user_change', args=[regular_user.pk]),
        {
            'email': regular_user.email,
            'name': 'Renamed',
            'status': User.STATUS_ACTIVE,
            'is_active': 'on',
        },
    )

    assert response.status_code == 302
    regular_user.refresh_from_db()
    assert regular_user.name == 'Renamed'
    assert regular_user.password == old_hash
