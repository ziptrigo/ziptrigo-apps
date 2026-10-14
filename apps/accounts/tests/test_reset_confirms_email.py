"""A password reset through an emailed link confirms the account's email (issue #125 review): the
link went to that address, so following it proves control of it. That is what lets the owner of an
account nobody confirmed finish by choosing a password, whether they came from forgot-password or
from the "finish setting up" email -- and what keeps a link from confirming an address it was
never sent to."""

import re

import pytest
from django.urls import reverse
from ninja_jwt.settings import api_settings

from apps.accounts.models import User
from apps.accounts.services.email_confirmation import get_email_confirmation_service
from apps.accounts.services.password_reset import PasswordResetService
from apps.accounts.tokens import PASSWORD_CLAIM, PasswordResetToken, password_fingerprint
from apps.core.models import EmailVerification

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
NEW_PASSWORD = {'password': 'newpass123', 'password_confirm': 'newpass123'}


@pytest.fixture
def unconfirmed(user):
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()
    return user


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    messages: list[dict] = []

    def fake_send_email(**kwargs):
        messages.append(kwargs)
        return 1, 0

    monkeypatch.setattr('apps.accounts.services.password_reset.send_email', fake_send_email)
    monkeypatch.setattr('apps.core.services.email_verification.send_email', fake_send_email)
    return messages


def _token(user: User) -> str:
    return str(PasswordResetToken.for_user(user))


def _url(token: str) -> str:
    return reverse('accounts:reset-password', args=[token])


def test_reset_confirms_an_unconfirmed_account(client, unconfirmed):
    response = client.post(_url(_token(unconfirmed)), NEW_PASSWORD, **HTMX)

    assert response['HX-Redirect'] == reverse('accounts:login')
    after = User.objects.get(pk=unconfirmed.pk)
    assert after.check_password('newpass123')
    assert after.email_confirmed is True
    assert after.email_confirmed_at is not None


def test_forgot_password_works_for_an_unconfirmed_account_end_to_end(client, unconfirmed, sent):
    """The route an owner has when the finish-setup email never reached them."""
    client.post(reverse('accounts:forgot-password'), {'email': unconfirmed.email}, **HTMX)

    (message,) = sent
    prefix = _url('x').removesuffix('x/')
    link = re.search(re.escape(prefix) + r'[^/\s]+/', message['text_body'])
    assert link is not None
    client.post(link.group(0), NEW_PASSWORD, **HTMX)

    after = User.objects.get(pk=unconfirmed.pk)
    assert after.email_confirmed is True
    assert after.check_password('newpass123')


def test_reset_through_the_api_confirms_too(api_client, unconfirmed):
    response = api_client.post(
        '/auth/reset-password', json={'token': _token(unconfirmed), **NEW_PASSWORD}
    )

    assert response.status_code == 200
    after = User.objects.get(pk=unconfirmed.pk)
    assert after.check_password('newpass123')
    assert after.email_confirmed is True
    assert after.email_confirmed_at is not None


def test_reset_leaves_a_confirmed_account_as_it_was(client, user):
    confirmed_at = user.email_confirmed_at

    client.post(_url(_token(user)), NEW_PASSWORD, **HTMX)

    after = User.objects.get(pk=user.pk)
    assert after.check_password('newpass123')
    assert after.email_confirmed is True
    assert after.email_confirmed_at == confirmed_at


def test_reset_invalidates_confirmation_links_still_out(client, unconfirmed, sent):
    get_email_confirmation_service().send_confirmation_email(unconfirmed)
    assert EmailVerification.objects.filter(invalidated_at__isnull=True).count() == 1

    client.post(_url(_token(unconfirmed)), NEW_PASSWORD, **HTMX)

    assert EmailVerification.objects.filter(invalidated_at__isnull=True).count() == 0


def test_reset_revokes_tokens_issued_under_the_old_password(client, unconfirmed):
    from apps.accounts.tokens import CustomAccessToken, matches_password

    old = CustomAccessToken.for_user(unconfirmed)

    client.post(_url(_token(unconfirmed)), NEW_PASSWORD, **HTMX)

    assert not matches_password(old, User.objects.get(pk=unconfirmed.pk))


# -- a link is bound to the address it was sent to --


def test_link_for_an_address_the_account_left_is_refused(client, unconfirmed):
    """The owner asked for a reset, then the account's address was changed (unconfirmed). The old
    link must neither confirm the new, unproven address nor reset the password."""
    stale = _token(unconfirmed)
    User.objects.filter(pk=unconfirmed.pk).update(email='elsewhere@example.com')
    before = User.objects.get(pk=unconfirmed.pk).password

    assert PasswordResetService.validate_token(stale) is None
    page = client.get(_url(stale))
    posted = client.post(_url(stale), NEW_PASSWORD, **HTMX)

    assert 'expired' in page.content.decode().lower()
    assert posted['HX-Redirect'] == _url(stale)
    after = User.objects.get(pk=unconfirmed.pk)
    assert after.password == before
    assert after.email_confirmed is False


def test_link_for_an_address_the_account_left_is_refused_by_the_api(api_client, unconfirmed):
    stale = _token(unconfirmed)
    User.objects.filter(pk=unconfirmed.pk).update(email='elsewhere@example.com')

    response = api_client.post('/auth/reset-password', json={'token': stale, **NEW_PASSWORD})

    assert response.status_code == 400
    assert User.objects.get(pk=unconfirmed.pk).email_confirmed is False


def test_address_changing_between_load_and_submit_is_not_confirmed(unconfirmed):
    """The page was loaded for the old address; by the time the form is submitted the account has
    a new one. The reset may still go through (the link was valid), but must not confirm it."""
    token = _token(unconfirmed)
    user = PasswordResetService.validate_token(token)
    assert user is not None
    User.objects.filter(pk=user.pk).update(email='elsewhere@example.com')

    PasswordResetService.reset_password(user, 'newpass123', token=token)

    after = User.objects.get(pk=user.pk)
    assert after.check_password('newpass123')
    assert after.email == 'elsewhere@example.com'
    assert after.email_confirmed is False


def test_link_without_the_address_claim_resets_but_does_not_confirm(client, unconfirmed):
    """A link without the address claim can't say which address it went to. (Every link sent
    before that claim also predates the password binding and is refused; this is a second guard.)
    """
    legacy = PasswordResetToken()
    legacy[api_settings.USER_ID_CLAIM] = str(unconfirmed.pk)
    legacy[PASSWORD_CLAIM] = password_fingerprint(unconfirmed)

    client.post(_url(str(legacy)), NEW_PASSWORD, **HTMX)

    after = User.objects.get(pk=unconfirmed.pk)
    assert after.check_password('newpass123')
    assert after.email_confirmed is False


def test_reset_email_link_is_bound_to_the_address(unconfirmed, sent):
    PasswordResetService(email_backend_classes=[]).request_reset(unconfirmed.email)

    (message,) = sent
    prefix = _url('x').removesuffix('x/')
    token = re.search(re.escape(prefix) + r'([^/\s]+)/', message['text_body'])
    assert token is not None
    assert PasswordResetToken(token.group(1))['email'] == unconfirmed.email


def test_account_can_log_in_once_a_reset_has_confirmed_it(client, unconfirmed):
    """Everything keyed on `email_confirmed` (the login check, and claim-on-login, which only
    claims for a confirmed address) sees the account as confirmed from then on."""
    refused = client.post(
        reverse('accounts:login'), {'email': unconfirmed.email, 'password': 'testpass123'}, **HTMX
    )
    assert refused.status_code == 422

    client.post(_url(_token(unconfirmed)), NEW_PASSWORD, **HTMX)
    login = client.post(
        reverse('accounts:login'), {'email': unconfirmed.email, 'password': 'newpass123'}, **HTMX
    )

    assert login.status_code == 200
    assert login['HX-Redirect'] != reverse('accounts:login')
    assert User.objects.get(pk=unconfirmed.pk).last_login is not None
