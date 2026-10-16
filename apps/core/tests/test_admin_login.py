"""The admin login page (`core/templates/admin/login.html`, an override of jazzmin's)."""

from typing import cast

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

PASSWORD = 'Admin-pass-123'


@pytest.fixture
def superuser():
    user = cast(User, UserFactory(is_staff=True, is_superuser=True))
    user.set_password(PASSWORD)
    user.save()
    return user


def test_bare_login_page_carries_next_to_the_admin_index(client):
    response = client.get(reverse('custom_admin:login'))

    assert response.status_code == 200
    assert f'name="next" value="{reverse("custom_admin:index")}"' in response.content.decode()


def test_login_from_a_bare_login_page_lands_on_the_admin_index(client, superuser):
    """The form posts the hidden `next` it was rendered with; without it the login view would fall
    back to `LOGIN_REDIRECT_URL` (`/accounts/profile/`, which doesn't exist)."""
    response = client.post(
        reverse('custom_admin:login'),
        {'username': superuser.email, 'password': PASSWORD, 'next': reverse('custom_admin:index')},
    )

    assert response.status_code == 302
    assert response.url == reverse('custom_admin:index')


def test_login_page_keeps_a_requested_next(client):
    response = client.get(f'{reverse("custom_admin:login")}?next=/admin/billing/')

    assert 'name="next" value="/admin/billing/"' in response.content.decode()


def test_failed_login_keeps_the_email(client, superuser):
    response = client.post(
        reverse('custom_admin:login'),
        {'username': superuser.email, 'password': 'wrong', 'next': reverse('custom_admin:index')},
    )

    assert response.status_code == 200
    assert f'value="{superuser.email}"' in response.content.decode()
