"""Signing up with an address that already has an account (issue #125).

The register page must answer the same for every valid submission, so it can't be used to learn
whether an address has an account; the owner of an existing one is emailed instead (a notice, or the
confirmation link again when the account was never confirmed). Nothing about the existing account
changes.
"""

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import existing_account
from apps.accounts.services.existing_account import (
    SUBJECT,
    handle_signup_for_existing_email,
    render_account_exists_email,
)
from apps.accounts.services.signup import EmailAlreadyRegistered

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
CONFIRMATION_SUBJECT = 'Confirm your ZipTrigo account email'


def _register(client, **overrides):
    payload = {
        'name': 'New User',
        'email': 'newuser@example.com',
        'password': 'password123',
        **overrides,
    }
    return client.post(reverse('accounts:register'), payload, **HTMX)


@pytest.fixture
def outbox(monkeypatch) -> list[dict]:
    """Every email handed to `send_email`: the notice, and the confirmation sent by
    `email_verification.start` (new account, or an unconfirmed one resent)."""
    sent: list[dict] = []

    def fake_send_email(**kwargs):
        sent.append(kwargs)
        return 1, 0

    monkeypatch.setattr('apps.accounts.services.existing_account.send_email', fake_send_email)
    monkeypatch.setattr('apps.core.services.email_verification.send_email', fake_send_email)
    return sent


@pytest.fixture
def register(client, django_capture_on_commit_callbacks):
    """`_register` with the notice's on-commit enqueue executed, as it is on a real request."""

    def _do(**overrides):
        with django_capture_on_commit_callbacks(execute=True):
            return _register(client, **overrides)

    return _do


def _notices(outbox, to):
    return [m for m in outbox if m['to'] == to and m['subject'] == SUBJECT]


# -- the visitor sees nothing different --


def test_existing_and_new_address_get_the_same_response(user, outbox):
    existing = _register(Client(), email=user.email)
    new = _register(Client(), email='brandnew@example.com')

    assert existing.status_code == new.status_code == 200
    assert existing['HX-Redirect'] == new['HX-Redirect'] == reverse('accounts:created')
    assert existing.content == new.content
    assert set(existing.headers) == set(new.headers)


def test_existing_and_new_address_get_the_same_redirect_without_htmx(user, outbox):
    payload = {'name': 'New User', 'password': 'password123'}
    existing = Client().post(reverse('accounts:register'), {**payload, 'email': user.email})
    new = Client().post(reverse('accounts:register'), {**payload, 'email': 'brandnew@example.com'})

    assert existing.status_code == new.status_code == 302
    assert existing['Location'] == new['Location'] == reverse('accounts:created')


def test_the_page_it_redirects_to_is_the_same_either_way(client):
    """There is one "check your email" page and it takes no state, so it can't differ."""
    first = client.get(reverse('accounts:created'))
    second = Client().get(reverse('accounts:created'))

    assert first.status_code == second.status_code == 200
    assert 'Check your email' in first.content.decode()
    assert first.content == second.content


def test_domain_case_variant_of_an_existing_address_gets_the_same_response(user, outbox, register):
    mixed_case_domain = 'testuser@EXAMPLE.COM'
    assert mixed_case_domain.lower() == user.email

    response = register(email=mixed_case_domain)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert User.objects.filter(email__iexact=user.email).count() == 1
    assert len(_notices(outbox, user.email)) == 1


# -- nothing about the existing account changes --


def test_existing_account_is_left_untouched(user, outbox, register):
    old_hash = user.password
    old_updated_at = user.updated_at

    register(email=user.email, name='Intruder', password='intruder123')

    after = User.objects.get(pk=user.pk)
    assert User.objects.count() == 1
    assert after.password == old_hash
    assert after.check_password('testpass123')
    assert after.name == 'Test User'
    assert after.email_confirmed is True
    assert after.updated_at == old_updated_at


def test_unconfirmed_account_is_left_untouched_too(user, outbox, register):
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()
    old_hash = User.objects.get(pk=user.pk).password

    register(email=user.email, name='Intruder', password='intruder123')

    after = User.objects.get(pk=user.pk)
    assert User.objects.count() == 1
    assert after.password == old_hash
    assert after.name == 'Test User'
    assert after.email_confirmed is False


# -- the owner is emailed --


def test_confirmed_password_account_gets_the_notice(user, outbox, register):
    register(email=user.email, name='Mallory <b>x</b>')

    notices = _notices(outbox, user.email)
    assert len(notices) == 1
    notice = notices[0]
    # Absolute links, built from BASE_URL.
    login_url = settings.BASE_URL.rstrip('/') + reverse('accounts:login')
    forgot_url = settings.BASE_URL.rstrip('/') + reverse('accounts:forgot-password')
    assert login_url.startswith('http')
    assert login_url in notice['text_body']
    assert forgot_url in notice['text_body']
    assert login_url in notice['html_body']
    assert forgot_url in notice['html_body']
    assert 'Google' not in notice['text_body']
    assert 'Test User' in notice['text_body']
    # Nothing the form was given makes it into the email.
    for part in (notice['subject'], notice['text_body'], notice['html_body']):
        assert 'Mallory' not in part
        assert 'intruder' not in part.lower()
    # And no confirmation email goes out for a confirmed account.
    assert [m for m in outbox if m['subject'] == CONFIRMATION_SUBJECT] == []


def test_google_account_is_told_to_use_google(user, outbox, register):
    user.google_sub = 'google-sub-1'
    user.set_unusable_password()
    user.save()

    register(email=user.email)

    (notice,) = _notices(outbox, user.email)
    assert 'Sign in with Google' in notice['text_body']
    assert 'Sign in with Google' in notice['html_body']
    assert reverse('accounts:forgot-password') not in notice['text_body']
    assert reverse('accounts:forgot-password') not in notice['html_body']
    assert reverse('accounts:login') in notice['text_body']


def test_google_account_that_also_has_a_password_gets_the_reset_link(user, outbox, register):
    user.google_sub = 'google-sub-1'
    user.save()

    register(email=user.email)

    (notice,) = _notices(outbox, user.email)
    assert reverse('accounts:forgot-password') in notice['text_body']


def test_unconfirmed_account_gets_the_confirmation_link_instead(user, outbox, register):
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()

    response = register(email=user.email)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert _notices(outbox, user.email) == []
    (confirmation,) = [m for m in outbox if m['to'] == user.email]
    assert confirmation['subject'] == CONFIRMATION_SUBJECT
    assert settings.BASE_URL.rstrip('/') + '/account/confirm-email/' in confirmation['text_body']


def test_unconfirmed_resend_does_not_spend_the_notice_budget(user, outbox, register, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'SIGNUP_EXISTING_EMAIL': (1, 3600)}
    user.email_confirmed = False
    user.save()

    register(email=user.email)
    user.email_confirmed = True
    user.save()
    register(email=user.email)

    assert len(_notices(outbox, user.email)) == 1


def test_unconfirmed_resend_respects_the_resend_cooldown_without_changing_the_response(
    user, outbox, register
):
    user.email_confirmed = False
    user.save()

    first = register(email=user.email)
    second = register(email=user.email)

    assert first.status_code == second.status_code == 200
    assert first['HX-Redirect'] == second['HX-Redirect']
    assert len([m for m in outbox if m['subject'] == CONFIRMATION_SUBJECT]) == 1


@pytest.mark.parametrize(
    'change',
    [{'is_active': False}, {'status': User.STATUS_INACTIVE}, {'status': User.STATUS_DELETED}],
)
def test_inactive_account_gets_nothing_but_the_same_response(user, outbox, register, change):
    for field, value in change.items():
        setattr(user, field, value)
    user.save()

    response = register(email=user.email)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert outbox == []


def test_new_address_gets_the_confirmation_email_and_no_notice(outbox, register):
    register(email='brandnew@example.com')

    (message,) = outbox
    assert message['to'] == 'brandnew@example.com'
    assert message['subject'] == CONFIRMATION_SUBJECT


# -- the notice is rate limited per address, silently --


def test_notice_is_sent_once_per_address_per_window(user, outbox, register, settings):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {
        **settings.RATELIMIT_RULES,
        'SIGNUP_EXISTING_EMAIL': (1, 3600),
        'SIGNUP_IP': (100, 3600),
    }

    first = register(email=user.email)
    second = register(email=user.email)
    # ... even when the address is spelled differently (the domain's case is normalized).
    third = register(email='testuser@EXAMPLE.com')

    assert len(_notices(outbox, user.email)) == 1
    assert first.status_code == second.status_code == third.status_code == 200
    assert first['HX-Redirect'] == second['HX-Redirect'] == third['HX-Redirect']
    assert first.content == second.content == third.content


def test_notice_budget_is_per_address_and_shared_across_ips(
    outbox, settings, django_capture_on_commit_callbacks
):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {
        **settings.RATELIMIT_RULES,
        'SIGNUP_EXISTING_EMAIL': (1, 3600),
        'SIGNUP_IP': (100, 3600),
    }
    one = User.objects.create_user(email='one@example.com', password='password123')
    two = User.objects.create_user(email='two@example.com', password='password123')
    for account in (one, two):
        account.email_confirmed = True
        account.save()

    with django_capture_on_commit_callbacks(execute=True):
        _register(Client(REMOTE_ADDR='203.0.113.1'), email='one@example.com')
        _register(Client(REMOTE_ADDR='203.0.113.2'), email='one@example.com')
        _register(Client(REMOTE_ADDR='203.0.113.2'), email='two@example.com')

    assert len(_notices(outbox, 'one@example.com')) == 1
    assert len(_notices(outbox, 'two@example.com')) == 1


def test_a_failing_notice_does_not_change_the_response(user, outbox, register, monkeypatch):
    class BrokenTask:
        name = 'broken'

        def enqueue(self, *args):
            raise RuntimeError('queue down')

    monkeypatch.setattr(existing_account, 'send_account_exists_email', BrokenTask())

    response = register(email=user.email)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')


def test_an_unexpected_error_while_handling_does_not_change_the_response(
    user, outbox, register, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError('database gone')

    monkeypatch.setattr(existing_account.ratelimit, 'hit_value', boom)

    response = register(email=user.email)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert outbox == []


def test_losing_the_signup_race_is_handled_like_any_other_duplicate(
    user, outbox, register, monkeypatch
):
    """`create_account` turns an `IntegrityError` from a concurrent signup into
    `EmailAlreadyRegistered`; the view must treat it exactly like the pre-check's."""

    def lose_the_race(**kwargs):
        raise EmailAlreadyRegistered(kwargs['email'])

    monkeypatch.setattr('apps.accounts.views.register.create_account', lose_the_race)

    response = register(email=user.email)

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert len(_notices(outbox, user.email)) == 1


# -- the notice task and its rendering --


def test_handler_ignores_an_address_nobody_has(outbox, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        handle_signup_for_existing_email('ghost@example.com')

    assert outbox == []


def test_task_does_not_send_if_the_account_moved_to_another_address(
    user, outbox, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        handle_signup_for_existing_email(user.email)
    user.email = 'moved@example.com'
    user.save()

    for callback in callbacks:
        callback()

    assert outbox == []


def test_notice_escapes_the_account_name(user):
    user.name = '<script>alert(1)</script>'

    subject, text_body, html_body = render_account_exists_email(user=user)

    assert subject == SUBJECT
    assert '&lt;script&gt;' in html_body
    assert '<script>' not in html_body
    assert user.email in text_body
