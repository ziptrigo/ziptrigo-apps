"""The emailed confirmation link (`accounts:confirm-email`): GET only shows a button, POST confirms.

Mail scanners and link previewers fetch a link's URL on their own; a GET that confirmed would let
the victim's own mail provider confirm an account registered with the attacker's password.
"""

import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.services.email_confirmation import get_email_confirmation_service

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


@pytest.fixture
def unconfirmed(user):
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save(update_fields=['email_confirmed', 'email_confirmed_at'])
    return user


@pytest.fixture
def token(unconfirmed, monkeypatch) -> str:
    sent = []
    monkeypatch.setattr(
        'apps.core.services.email_verification.send_email',
        lambda **kwargs: sent.append(kwargs) or (1, 0),
    )
    get_email_confirmation_service().send_confirmation_email(unconfirmed)
    match = re.search(r'confirm-email/([^/\s]+)/', sent[-1]['text_body'])
    assert match is not None
    return match.group(1)


def test_get_shows_a_button_and_does_not_confirm(client, unconfirmed, token):
    url = reverse('accounts:confirm-email', args=[token])

    response = client.get(url)

    assert response.status_code == 200
    html = response.content.decode()
    assert 'Confirm my email' in html
    assert 'csrfmiddlewaretoken' in html
    assert f'action="{url}"' in html
    unconfirmed.refresh_from_db()
    assert not unconfirmed.email_confirmed
    # The token was not consumed either: the click still works.
    assert client.post(url).status_code == 302


def test_get_with_a_bogus_token_does_not_touch_the_token(client):
    response = client.get(reverse('accounts:confirm-email', args=['not-a-real-token']))

    assert response.status_code == 200
    assert 'Confirm my email' in response.content.decode()


def test_post_confirms_and_redirects(client, unconfirmed, token):
    response = client.post(reverse('accounts:confirm-email', args=[token]))

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:email-confirmed')
    unconfirmed.refresh_from_db()
    assert unconfirmed.email_confirmed


def test_post_twice_is_harmless(client, unconfirmed, token):
    url = reverse('accounts:confirm-email', args=[token])

    assert client.post(url).status_code == 302
    assert client.post(url).status_code == 302
    unconfirmed.refresh_from_db()
    assert unconfirmed.email_confirmed


def test_post_without_csrf_token_is_rejected(unconfirmed, token):
    client = Client(enforce_csrf_checks=True)

    response = client.post(reverse('accounts:confirm-email', args=[token]))

    assert response.status_code == 403
    unconfirmed.refresh_from_db()
    assert not unconfirmed.email_confirmed


def test_post_with_csrf_token_from_the_page_confirms(unconfirmed, token):
    client = Client(enforce_csrf_checks=True)
    url = reverse('accounts:confirm-email', args=[token])
    page = client.get(url).content.decode()
    csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page)
    assert csrf is not None

    response = client.post(url, {'csrfmiddlewaretoken': csrf.group(1)})

    assert response.status_code == 302
    unconfirmed.refresh_from_db()
    assert unconfirmed.email_confirmed


def test_post_with_an_invalid_token_shows_the_expired_page(client, unconfirmed):
    response = client.post(reverse('accounts:confirm-email', args=['not-a-real-token']))

    assert response.status_code == 200
    assert 'Link Expired' in response.content.decode()
    unconfirmed.refresh_from_db()
    assert not unconfirmed.email_confirmed
