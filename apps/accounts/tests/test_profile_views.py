"""The account page's session-authenticated profile form."""

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}


def test_account_page_renders_profile_form(client, user):
    client.force_login(user)

    response = client.get(reverse('account-page'))

    assert response.status_code == 200
    content = response.content.decode()
    assert f'hx-post="{reverse("profile-update")}"' in content
    assert 'value="Test User"' in content


def test_updates_name_and_returns_form_partial(client, user):
    client.force_login(user)

    response = client.post(reverse('profile-update'), {'name': 'New Name'}, **HTMX)

    assert response.status_code == 200
    content = response.content.decode()
    assert 'Profile updated successfully.' in content
    assert 'value="New Name"' in content
    assert '<html' not in content
    user.refresh_from_db()
    assert user.name == 'New Name'


def test_email_cannot_be_changed(client, user):
    client.force_login(user)

    client.post(reverse('profile-update'), {'name': 'X', 'email': 'new@example.com'}, **HTMX)

    user.refresh_from_db()
    assert user.email == 'testuser@example.com'


def test_blank_name_returns_errors(client, user):
    client.force_login(user)

    response = client.post(reverse('profile-update'), {'name': ''}, **HTMX)

    assert response.status_code == 422
    assert 'Please fill in your name.' in response.content.decode()
    user.refresh_from_db()
    assert user.name == 'Test User'


def test_without_htmx_redirects_to_account_page(client, user):
    client.force_login(user)

    response = client.post(reverse('profile-update'), {'name': 'New Name'})

    assert response.status_code == 302
    assert response['Location'] == reverse('account-page')


def test_requires_login(client):
    response = client.post(reverse('profile-update'), {'name': 'New Name'})

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('login-page'))
