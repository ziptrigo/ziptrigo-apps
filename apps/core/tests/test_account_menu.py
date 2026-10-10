"""The nav's account area: a user-icon menu (Settings, Logout) when logged in, else a Login link."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_account_menu_for_logged_in_user(client, user):
    client.force_login(user)
    html = client.get(reverse('core:home')).content.decode()
    assert 'aria-label="Account menu"' in html
    assert f'href="{reverse("accounts:account")}"' in html
    assert 'Settings' in html
    assert f'action="{reverse("accounts:logout")}"' in html
    assert 'Logout' in html


def test_login_link_for_anonymous_visitor(client):
    html = client.get(reverse('core:home')).content.decode()
    assert 'aria-label="Account menu"' not in html
    assert f'href="{reverse("accounts:login")}"' in html
