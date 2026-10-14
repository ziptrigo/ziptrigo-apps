"""How the letter case of an email address is handled (audit #15).

Accounts are stored with the domain lower-cased (`UserManager.normalize_email`), the local part as
typed. The web forms normalize what they are given the same way (like the API's `EmailStr`), so a
mixed-case domain finds the account; and no signup or email change may create a second row that
differs from an existing address only by case (Google sign-in matches `iexact` and would refuse
both rows as a conflict forever).
"""

from datetime import timedelta

import pytest
from django.contrib.auth import SESSION_KEY
from django.urls import reverse
from django.utils import timezone

from apps.accounts.forms.admin import UserAdminChangeForm, UserAdminCreationForm
from apps.accounts.models import User
from apps.accounts.services import google_oauth
from apps.accounts.services.email_confirmation import get_email_confirmation_service
from apps.accounts.services.existing_account import (
    FINISH_SETUP_SUBJECT,
    handle_signup_for_existing_email,
)
from apps.accounts.tokens import CustomAccessToken
from apps.core.models import EmailVerification

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


@pytest.fixture
def legacy_pair(user):
    """Two rows that differ only by case, as could exist before signup checked for it: the
    fixture user `testuser@example.com` (older) and `TestUser@example.com` (newer)."""
    newer = User.objects.create_user(email='TestUser@example.com', password='x1y2z3w4')
    newer.email_confirmed = True
    newer.save(update_fields=['email_confirmed'])
    now = timezone.now()
    User.objects.filter(pk=user.pk).update(created_at=now - timedelta(days=1))
    User.objects.filter(pk=newer.pk).update(created_at=now)
    return user, newer


@pytest.fixture
def notices(monkeypatch) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.accounts.services.existing_account.send_email', lambda **kw: sent.append(kw)
    )
    return sent


def _handle(email, django_capture_on_commit_callbacks, caplog):
    """Run the handler as a request would, and prove it did not swallow an error on the way:
    it never raises, so a lookup that blew up would otherwise look like a pass."""
    with django_capture_on_commit_callbacks(execute=True):
        handle_signup_for_existing_email(email, 'password123')
    assert 'Could not handle a signup' not in caplog.text


def test_existing_account_lookup_survives_legacy_duplicates(
    legacy_pair, notices, django_capture_on_commit_callbacks, caplog
):
    """Rows that differ only by case can already exist: no crash (a `.get()` would raise
    `MultipleObjectsReturned`, which the handler swallows), and exactly one notice goes out.
    With no exact match the oldest row is the account."""
    older, _newer = legacy_pair

    _handle('TESTUSER@example.com', django_capture_on_commit_callbacks, caplog)

    assert [m['to'] for m in notices] == [older.email]


def test_existing_account_lookup_prefers_the_exact_spelling_among_legacy_duplicates(
    legacy_pair, notices, django_capture_on_commit_callbacks, caplog
):
    _older, newer = legacy_pair

    _handle(newer.email, django_capture_on_commit_callbacks, caplog)

    assert [m['to'] for m in notices] == ['TestUser@example.com']


def test_existing_account_lookup_exact_match_wins_for_the_older_row_too(
    legacy_pair, notices, django_capture_on_commit_callbacks, caplog
):
    older, _newer = legacy_pair

    _handle(older.email, django_capture_on_commit_callbacks, caplog)

    assert [m['to'] for m in notices] == ['testuser@example.com']


def test_signup_with_a_case_variant_locks_down_the_stored_unconfirmed_row(
    client, user, monkeypatch, django_capture_on_commit_callbacks
):
    """The locked-down and mailed row is the stored one, whatever spelling was typed."""
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.accounts.services.existing_account.send_email', lambda **kw: sent.append(kw)
    )
    monkeypatch.setattr('apps.core.services.email_verification.send_email', lambda **kw: (1, 0))
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()
    get_email_confirmation_service().send_confirmation_email(user)
    pending = EmailVerification.objects.filter(
        email__iexact=user.email, confirmed_at__isnull=True, invalidated_at__isnull=True
    )
    assert pending.exists()

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse('accounts:register'),
            {'name': 'Intruder', 'email': 'TestUser@Example.com', 'password': 'intruder123'},
            **HTMX,
        )

    assert response.status_code == 200
    assert User.objects.count() == 1
    after = User.objects.get(pk=user.pk)
    assert not after.has_usable_password()
    assert after.email == 'testuser@example.com'
    assert not pending.exists()
    assert [(m['to'], m['subject']) for m in sent] == [
        ('testuser@example.com', FINISH_SETUP_SUBJECT)
    ]


# -- the admin forms --


def test_admin_creation_form_refuses_a_case_variant_of_an_existing_address(user):
    form = UserAdminCreationForm(
        {
            'email': 'TestUser@example.com',
            'name': 'Dup',
            'usable_password': 'true',
            'password1': 'S3cure-pass-42',
            'password2': 'S3cure-pass-42',
        }
    )

    assert not form.is_valid()
    assert form.errors['email'] == ['A user with this email already exists.']


def test_admin_creation_form_normalizes_the_domain(db):
    form = UserAdminCreationForm(
        {
            'email': 'New@Example.COM',
            'name': 'New',
            'usable_password': 'true',
            'password1': 'S3cure-pass-42',
            'password2': 'S3cure-pass-42',
        }
    )

    assert form.is_valid(), form.errors
    assert form.save().email == 'New@example.com'


def test_admin_change_form_refuses_moving_to_a_case_variant_of_another_address(user, admin_user):
    admin_user.email = 'admin@example.com'
    admin_user.save(update_fields=['email'])
    form = UserAdminChangeForm(
        {
            'email': 'ADMIN@example.com',
            'name': 'x',
            'status': User.STATUS_ACTIVE,
            'is_active': 'on',
        },
        instance=user,
    )

    assert not form.is_valid()
    assert 'email' in form.errors


def test_admin_change_form_leaves_one_of_two_legacy_rows_editable(legacy_pair):
    _older, newer = legacy_pair
    form = UserAdminChangeForm(
        {
            'email': newer.email,
            'name': 'Renamed',
            'status': User.STATUS_ACTIVE,
            'is_active': 'on',
        },
        instance=newer,
    )

    assert form.is_valid(), form.errors


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

    response = _put(client, user, {'email': 'taken@example.com', 'current_password': 'testpass123'})

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
