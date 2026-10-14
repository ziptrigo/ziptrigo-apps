"""How the letter case of an email address is handled (audit #15).

Accounts are stored with the domain lower-cased (`UserManager.normalize_email`), the local part as
typed. The web forms normalize what they are given the same way (like the API's `EmailStr`), so a
mixed-case domain finds the account; and no signup or email change may create a second row that
differs from an existing address only by case (Google sign-in matches `iexact` and would refuse
both rows as a conflict forever).
"""

import pytest
from django.contrib.auth import SESSION_KEY
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import google_oauth
from apps.accounts.services.existing_account import handle_signup_for_existing_email
from apps.accounts.tokens import CustomAccessToken

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
PASSWORD = 'testpass123'


def test_web_login_accepts_a_mixed_case_domain(client, user):
    response = client.post(
        reverse('accounts:login'), {'email': 'testuser@Example.COM', 'password': PASSWORD}, **HTMX
    )

    assert response.status_code == 200
    assert client.session[SESSION_KEY] == str(user.pk)


def test_web_forgot_password_finds_the_account_by_a_mixed_case_domain(client, user, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.accounts.services.password_reset.send_email', lambda **kw: sent.append(kw)
    )

    response = client.post(
        reverse('accounts:forgot-password'), {'email': 'testuser@Example.COM'}, **HTMX
    )

    assert response.status_code == 200
    assert [m['to'] for m in sent] == [user.email]


def test_web_resend_confirmation_finds_the_account_by_a_mixed_case_domain(
    client, user, monkeypatch
):
    user.email_confirmed = False
    user.save(update_fields=['email_confirmed'])
    sent: list[str] = []
    monkeypatch.setattr(
        'apps.accounts.services.email_confirmation.EmailConfirmationService.send_confirmation_email',
        lambda self, u: sent.append(u.email),
    )

    response = client.post(
        reverse('accounts:resend-confirmation'), {'email': 'testuser@Example.COM'}, **HTMX
    )

    assert response.status_code == 200
    assert sent == [user.email]


def test_login_does_not_match_a_different_local_part_case(client, user):
    """The local part stays exact on login: only the domain is normalized."""
    response = client.post(
        reverse('accounts:login'), {'email': 'TestUser@example.com', 'password': PASSWORD}, **HTMX
    )

    assert response.status_code == 422
    assert SESSION_KEY not in client.session


# -- signup --


def test_signup_with_a_case_variant_of_an_existing_address_creates_no_user(
    client, user, django_capture_on_commit_callbacks
):
    payload = {'name': 'Intruder', 'password': 'password123'}
    with django_capture_on_commit_callbacks(execute=True):
        variant = client.post(
            reverse('accounts:register'), {**payload, 'email': 'TestUser@Example.com'}, **HTMX
        )
        new = client.post(
            reverse('accounts:register'), {**payload, 'email': 'brandnew@example.com'}, **HTMX
        )

    assert variant.status_code == new.status_code == 200
    assert variant['HX-Redirect'] == new['HX-Redirect'] == reverse('accounts:created')
    assert User.objects.filter(email__iexact='testuser@example.com').count() == 1
    assert User.objects.filter(email__iexact='brandnew@example.com').count() == 1


def test_signup_with_a_case_variant_sends_the_existing_account_its_notice(
    client, user, monkeypatch, django_capture_on_commit_callbacks
):
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.accounts.services.existing_account.send_email', lambda **kw: sent.append(kw)
    )

    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse('accounts:register'),
            {'name': 'x', 'email': 'TESTUSER@example.com', 'password': 'password123'},
            **HTMX,
        )

    assert [m['to'] for m in sent] == [user.email]


def test_existing_account_lookup_survives_legacy_duplicates(user):
    """Rows that differ only by case can already exist; the oldest is the account, no crash."""
    User.objects.create_user(email='TestUser@example.com', password='x1y2z3w4')

    handle_signup_for_existing_email('testuser@example.com', 'password123')  # must not raise


# -- PUT /api/account --


def _put(client, user, payload):
    return client.put(
        '/api/account',
        payload,
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {CustomAccessToken.for_user(user)}',
    )


def test_email_change_to_a_case_variant_of_another_account_is_refused(client, user):
    User.objects.create_user(email='Taken@example.com', password='password123')

    response = _put(client, user, {'email': 'taken@example.com'})

    assert response.status_code == 400
    assert response.json()['detail'] == 'Email already in use'
    user.refresh_from_db()
    assert user.email == 'testuser@example.com'


# -- Google sign-in keeps working --


def test_google_sign_in_still_links_the_one_account_after_a_refused_variant_signup(
    client, user, django_capture_on_commit_callbacks
):
    """The point of the iexact pre-check: no second row to make the `iexact` match ambiguous."""
    user.email = 'testuser@gmail.com'
    user.save(update_fields=['email'])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse('accounts:register'),
            {'name': 'x', 'email': 'TestUser@Gmail.com', 'password': 'password123'},
            **HTMX,
        )

    identity = google_oauth.GoogleIdentity(
        sub='sub-1', email='TestUser@Gmail.com', email_verified=True, name='T'
    )
    resolved = google_oauth.resolve_user(identity)

    assert resolved.pk == user.pk
    assert User.objects.count() == 1
