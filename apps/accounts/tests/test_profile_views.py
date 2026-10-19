"""The account page's session-authenticated profile form."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def test_account_page_renders_profile_form(client, user):
    client.force_login(user)

    response = client.get(reverse('accounts:account'))

    assert response.status_code == 200
    content = response.content.decode()
    assert f'hx-post="{reverse("accounts:profile-update")}"' in content
    assert 'value="Test User"' in content


def test_updates_name_and_returns_form_partial(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:profile-update'), {'name': 'New Name'}, **HTMX)

    assert response.status_code == 200
    content = response.content.decode()
    assert 'Profile updated successfully.' in content
    assert 'value="New Name"' in content
    assert '<html' not in content
    user.refresh_from_db()
    assert user.name == 'New Name'


def test_email_cannot_be_changed(client, user):
    client.force_login(user)

    client.post(
        reverse('accounts:profile-update'), {'name': 'X', 'email': 'new@example.com'}, **HTMX
    )

    user.refresh_from_db()
    assert user.email == 'testuser@example.com'


def test_blank_name_returns_errors(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:profile-update'), {'name': ''}, **HTMX)

    assert response.status_code == 422
    assert 'Please fill in your name.' in response.content.decode()
    user.refresh_from_db()
    assert user.name == 'Test User'


def test_without_htmx_redirects_to_account_page(client, user):
    client.force_login(user)

    response = client.post(reverse('accounts:profile-update'), {'name': 'New Name'})

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')


def test_requires_login(client):
    response = client.post(reverse('accounts:profile-update'), {'name': 'New Name'})

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))


def test_account_page_shows_password_change_for_a_password_account(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert 'Change Password' in content
    assert 'no-password-note' not in content


def test_account_page_explains_no_password_for_a_google_created_account(client, user):
    user.set_unusable_password()
    user.save()
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert 'Change Password' not in content
    assert "You signed in with Google, so this account doesn't have a password." in content
    assert f'href="{reverse("accounts:forgot-password")}"' in content
    assert 'Set a password' in content


def test_account_page_names_microsoft_for_a_microsoft_created_account(client, user):
    user.microsoft_sub = 'microsoft-sub-1'
    user.set_unusable_password()
    user.save()
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert "You signed in with Microsoft, so this account doesn't have a password." in content
    assert 'Google' not in content
