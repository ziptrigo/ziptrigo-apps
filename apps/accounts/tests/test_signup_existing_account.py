"""Signing up with an address that already has an account (issue #125).

The register page must answer the same for every valid submission, so it can't be used to learn
whether an address has an account; the owner of an existing one is emailed instead (a notice for a
confirmed account; for one never confirmed, a "finish setting up" link after locking the account
down, so its creator's password can't be confirmed into a login). Nothing else about the existing
account changes.
"""

import re
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.auth import authenticate
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services import existing_account
from apps.accounts.services.email_confirmation import get_email_confirmation_service
from apps.accounts.services.existing_account import (
    FINISH_SETUP_SUBJECT,
    SUBJECT,
    handle_signup_for_existing_email,
    render_account_exists_email,
    render_finish_setup_email,
)
from apps.accounts.services.signup import EmailAlreadyRegistered
from apps.accounts.tokens import CustomAccessToken, matches_password
from apps.core.models import EmailVerification
from apps.core.ratelimit import limiter

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
    """Every email handed to `send_email`: the notice and the "finish setting up" email, the
    confirmation sent by `email_verification.start` (a new account, or a resend), and the
    forgot-password one."""
    sent: list[dict] = []

    def fake_send_email(**kwargs):
        sent.append(kwargs)
        return 1, 0

    monkeypatch.setattr('apps.accounts.services.existing_account.send_email', fake_send_email)
    monkeypatch.setattr('apps.accounts.services.password_reset.send_email', fake_send_email)
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


def _finish_setup_emails(outbox, to):
    return [m for m in outbox if m['to'] == to and m['subject'] == FINISH_SETUP_SUBJECT]


def _set_password_path(message) -> str:
    """The path of the set-password link in an email's text body."""
    prefix = reverse('accounts:reset-password', args=['x']).removesuffix('x/')
    match = re.search(re.escape(prefix) + r'[^/\s]+/', message['text_body'])
    assert match is not None, message['text_body']
    return match.group(0)


def _confirmation_token(message) -> str:
    match = re.search(r'confirm-email/([^/\s]+)/', message['text_body'])
    assert match is not None, message['text_body']
    return match.group(1)


def _unconfirm(user):
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()


# -- the visitor sees nothing different --


def _comparable_headers(response) -> dict[str, str]:
    return {name: value for name, value in response.headers.items() if name.lower() != 'date'}


def test_existing_and_new_address_get_the_same_response(user, outbox):
    existing = _register(Client(), email=user.email)
    new = _register(Client(), email='brandnew@example.com')

    assert existing.status_code == new.status_code == 200
    assert existing['HX-Redirect'] == new['HX-Redirect'] == reverse('accounts:created')
    assert existing.content == new.content
    # Every header value, bar the ones that vary per response by nature.
    assert _comparable_headers(existing) == _comparable_headers(new)
    assert set(existing.cookies) == set(new.cookies)


def test_existing_and_new_address_get_the_same_redirect_without_htmx(user, outbox):
    payload = {'name': 'New User', 'password': 'password123'}
    existing = Client().post(reverse('accounts:register'), {**payload, 'email': user.email})
    new = Client().post(reverse('accounts:register'), {**payload, 'email': 'brandnew@example.com'})

    assert existing.status_code == new.status_code == 302
    assert existing['Location'] == new['Location'] == reverse('accounts:created')
    assert existing.content == new.content
    assert _comparable_headers(existing) == _comparable_headers(new)


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


def test_unconfirmed_account_keeps_its_name_and_email(user, outbox, register):
    _unconfirm(user)

    register(email=user.email, name='Intruder', password='intruder123')

    after = User.objects.get(pk=user.pk)
    assert User.objects.count() == 1
    assert after.name == 'Test User'
    assert after.email == user.email
    assert after.email_confirmed is False
    # ... and the visitor's password is not stored in its place.
    assert not after.check_password('intruder123')


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


def test_microsoft_account_is_told_to_use_microsoft(user, outbox, register):
    user.microsoft_sub = 'microsoft-sub-1'
    user.set_unusable_password()
    user.save()

    register(email=user.email)

    (notice,) = _notices(outbox, user.email)
    assert 'Sign in with Microsoft' in notice['text_body']
    assert 'Sign in with Microsoft' in notice['html_body']
    assert 'Google' not in notice['text_body']
    assert reverse('accounts:forgot-password') not in notice['text_body']
    assert reverse('accounts:forgot-password') not in notice['html_body']
    assert reverse('accounts:login') in notice['text_body']


def test_microsoft_account_that_also_has_a_password_gets_the_reset_link(user, outbox, register):
    user.microsoft_sub = 'microsoft-sub-1'
    user.save()

    register(email=user.email)

    (notice,) = _notices(outbox, user.email)
    assert 'Sign in with Microsoft' not in notice['text_body']
    assert reverse('accounts:forgot-password') in notice['text_body']


def test_unconfirmed_account_gets_the_finish_setup_email_instead(user, outbox, register):
    _unconfirm(user)

    response = register(email=user.email, name='Intruder <b>x</b>', password='intruder123')

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert _notices(outbox, user.email) == []
    # No confirmation link is sent for it: that would confirm the account with its creator's
    # password.
    assert [m for m in outbox if m['subject'] == CONFIRMATION_SUBJECT] == []
    (email,) = _finish_setup_emails(outbox, user.email)
    assert _set_password_path(email) in email['html_body']
    assert settings.BASE_URL.rstrip('/') + _set_password_path(email) in email['text_body']


def test_finish_setup_email_and_notice_share_one_budget(user, outbox, register, settings):
    """The finish-setup email and the notice share one per-address budget."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {
        **settings.RATELIMIT_RULES,
        'SIGNUP_EXISTING_EMAIL': (1, 3600),
        'SIGNUP_IP': (100, 3600),
    }
    _unconfirm(user)

    register(email=user.email)
    user.email_confirmed = True
    user.save()
    register(email=user.email)

    assert len(_finish_setup_emails(outbox, user.email)) == 1
    assert _notices(outbox, user.email) == []


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
    user, outbox, register, monkeypatch, caplog
):
    def boom(*args, **kwargs):
        raise RuntimeError('database gone')

    monkeypatch.setattr(existing_account.ratelimit, 'hit_value', boom)

    response = register(email=user.email, password='typedpass123')

    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert outbox == []
    # The log names the account by id, and carries neither the address nor what was typed.
    assert 'Could not handle a signup' in caplog.text
    assert str(user.pk) in caplog.text
    assert user.email not in caplog.text
    assert 'typedpass123' not in caplog.text


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
        handle_signup_for_existing_email('ghost@example.com', 'password123')

    assert outbox == []


def test_task_does_not_send_if_the_account_moved_to_another_address(
    user, outbox, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        handle_signup_for_existing_email(user.email, 'password123')
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


def test_unknown_address_error_log_has_no_user_id(outbox, caplog, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('database gone')

    monkeypatch.setattr(existing_account, 'make_password', boom)

    handle_signup_for_existing_email('ghost@example.com', 'typedpass123')

    assert 'user None' in caplog.text
    assert 'ghost@example.com' not in caplog.text


# -- an unconfirmed account is locked down, not handed back to whoever created it --


def test_unconfirmed_account_response_matches_a_new_address_too(user, outbox):
    _unconfirm(user)

    existing = _register(Client(), email=user.email)
    new = _register(Client(), email='brandnew@example.com')

    assert existing.status_code == new.status_code == 200
    assert existing.content == new.content
    assert _comparable_headers(existing) == _comparable_headers(new)


def test_unconfirmed_account_password_is_made_unusable(user, outbox, register):
    _unconfirm(user)

    register(email=user.email, name='Intruder', password='intruder123')

    after = User.objects.get(pk=user.pk)
    assert not after.has_usable_password()
    assert authenticate(email=user.email, password='testpass123') is None
    assert authenticate(email=user.email, password='intruder123') is None


def test_unconfirmed_account_is_locked_down_even_over_the_email_budget(
    user, outbox, register, settings
):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {
        **settings.RATELIMIT_RULES,
        'SIGNUP_EXISTING_EMAIL': (1, 3600),
        'SIGNUP_IP': (100, 3600),
    }
    _unconfirm(user)
    first = register(email=user.email)
    assert len(_finish_setup_emails(outbox, user.email)) == 1
    # The budget is spent. Give the account a usable password again (as a later attempt to claim
    # it, or an admin, might have) and try once more: it is locked down all the same.
    outbox.clear()
    user.refresh_from_db()
    user.set_password('another123')
    user.save()

    second = register(email=user.email)

    assert outbox == []
    assert not User.objects.get(pk=user.pk).has_usable_password()
    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_pending_confirmation_links_stop_working(user, outbox, register):
    _unconfirm(user)
    get_email_confirmation_service().send_confirmation_email(user)
    (confirmation,) = [m for m in outbox if m['subject'] == CONFIRMATION_SUBJECT]
    old_token = _confirmation_token(confirmation)

    register(email=user.email)

    assert get_email_confirmation_service().confirm_token(old_token) is None
    assert User.objects.get(pk=user.pk).email_confirmed is False
    assert not EmailVerification.objects.filter(
        email=user.email, confirmed_at__isnull=True, invalidated_at__isnull=True
    ).exists()


def test_tokens_issued_under_the_old_password_stop_working(user, outbox, register):
    _unconfirm(user)
    token = CustomAccessToken.for_user(user)
    assert matches_password(token, User.objects.get(pk=user.pk))

    register(email=user.email)

    assert not matches_password(token, User.objects.get(pk=user.pk))


def test_an_unusable_password_is_not_rewritten_on_every_attempt(user, outbox, register):
    _unconfirm(user)
    register(email=user.email)
    locked = User.objects.get(pk=user.pk).password

    register(email=user.email)

    assert User.objects.get(pk=user.pk).password == locked


def test_unconfirmed_account_linked_to_google_is_left_alone(user, outbox, register):
    """Only an email change can produce one; someone is using that account, so a signup must
    neither lock it nor mail a way in."""
    _unconfirm(user)
    user.google_sub = 'google-sub-1'
    user.save()
    before = User.objects.get(pk=user.pk).password

    response = register(email=user.email)

    assert response.status_code == 200
    assert User.objects.get(pk=user.pk).password == before
    assert outbox == []


def test_unconfirmed_account_linked_to_microsoft_is_left_alone(user, outbox, register):
    """Same as for Google: only an email change can produce one, and someone is using it."""
    _unconfirm(user)
    user.microsoft_sub = 'microsoft-sub-1'
    user.save()
    before = User.objects.get(pk=user.pk).password

    response = register(email=user.email)

    assert response.status_code == 200
    assert User.objects.get(pk=user.pk).password == before
    assert outbox == []


@pytest.mark.parametrize('flag', ['is_staff', 'is_superuser'])
def test_unconfirmed_staff_account_is_left_alone(user, outbox, register, flag):
    """`createsuperuser` leaves the email unconfirmed; a stranger must not be able to lock an
    admin out by submitting their address."""
    _unconfirm(user)
    setattr(user, flag, True)
    user.save()
    before = User.objects.get(pk=user.pk).password

    response = register(email=user.email)

    assert response.status_code == 200
    assert User.objects.get(pk=user.pk).password == before
    assert User.objects.get(pk=user.pk).check_password('testpass123')
    assert outbox == []


def test_finish_setup_email_has_nothing_from_the_account_or_the_form(user, outbox, register):
    _unconfirm(user)
    user.name = 'Mallory <script>alert(1)</script>'
    user.save()

    register(email=user.email, name='Intruder <b>x</b>', password='intruder123')

    (message,) = _finish_setup_emails(outbox, user.email)
    for part in (message['subject'], message['text_body'], message['html_body']):
        assert 'Mallory' not in part
        assert 'script' not in part
        assert 'Intruder' not in part
        assert 'intruder123' not in part
    assert message['html_body'].count('<form') == 0
    assert 'Hi there,' in message['text_body']
    assert user.email in message['text_body']


def test_finish_setup_email_renders_without_a_user():
    subject, text_body, html_body = render_finish_setup_email(
        email='someone@example.com', set_password_url='https://example.com/reset-password/t/'
    )

    assert subject == FINISH_SETUP_SUBJECT
    assert 'https://example.com/reset-password/t/' in text_body
    assert 'https://example.com/reset-password/t/' in html_body
    assert str(settings.PASSWORD_RESET_TOKEN_TTL_HOURS) in text_body


@pytest.mark.parametrize(
    'change',
    [{'is_active': False}, {'status': User.STATUS_INACTIVE}, {'status': User.STATUS_DELETED}],
)
def test_inactive_unconfirmed_account_is_left_alone_too(user, outbox, register, change):
    _unconfirm(user)
    for field, value in change.items():
        setattr(user, field, value)
    user.save()
    before = User.objects.get(pk=user.pk).password

    register(email=user.email)

    assert User.objects.get(pk=user.pk).password == before
    assert outbox == []


def test_finish_setup_task_sends_nothing_once_the_account_is_confirmed_or_moved(
    user, outbox, django_capture_on_commit_callbacks
):
    _unconfirm(user)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        handle_signup_for_existing_email(user.email, 'password123')
    old_address = user.email

    User.objects.filter(pk=user.pk).update(email_confirmed=True)
    for callback in callbacks:
        callback()
    assert outbox == []

    User.objects.filter(pk=user.pk).update(email_confirmed=False, email='moved@example.com')
    existing_account.send_finish_setup_email.call(str(user.pk), old_address)
    assert outbox == []


def test_the_pre_hijack_scenario_end_to_end(outbox, register, django_capture_on_commit_callbacks):
    """Issue #125 review: an attacker registers a victim's address with a password they know and
    leaves it unconfirmed; the victim then signs up themselves. The victim must end up with a
    working account under a password of their own, and the attacker with nothing."""
    victim = 'victim@gmail.com'
    register(email=victim, name='Mallory', password='attackerpw1')
    attacker_account = User.objects.get(email=victim)
    assert attacker_account.email_confirmed is False
    attacker_token = CustomAccessToken.for_user(attacker_account)
    (original,) = [m for m in outbox if m['subject'] == CONFIRMATION_SUBJECT]
    old_link = _confirmation_token(original)
    outbox.clear()

    response = register(email=victim, name='Victim', password='victimpw2')

    # The victim is shown the same page as anyone.
    assert response.status_code == 200
    assert response['HX-Redirect'] == reverse('accounts:created')
    assert User.objects.filter(email=victim).count() == 1
    # Neither password works, and the attacker's tokens are dead.
    assert authenticate(email=victim, password='attackerpw1') is None
    assert authenticate(email=victim, password='victimpw2') is None
    assert not matches_password(attacker_token, User.objects.get(email=victim))
    # The confirmation link the attacker's signup mailed to the victim no longer confirms it.
    assert get_email_confirmation_service().confirm_token(old_link) is None
    assert User.objects.get(email=victim).email_confirmed is False
    # What the victim got is a way to choose a password -- and nothing else.
    assert [m for m in outbox if m['subject'] == CONFIRMATION_SUBJECT] == []
    (setup,) = _finish_setup_emails(outbox, victim)
    for part in (setup['text_body'], setup['html_body']):
        assert 'Mallory' not in part
        assert 'Victim' not in part
        assert 'attackerpw1' not in part
        assert 'victimpw2' not in part
    link = _set_password_path(setup)

    visitor = Client()
    assert visitor.get(link).status_code == 200
    done = visitor.post(link, {'password': 'chosenpw3', 'password_confirm': 'chosenpw3'}, **HTMX)
    assert done['HX-Redirect'] == reverse('accounts:login')

    after = User.objects.get(email=victim)
    assert after.email_confirmed is True
    assert after.email_confirmed_at is not None
    assert authenticate(email=victim, password='chosenpw3') == after
    assert authenticate(email=victim, password='attackerpw1') is None
    # And it is a working account: session login ...
    session_client = Client()
    login = session_client.post(
        reverse('accounts:login'), {'email': victim, 'password': 'chosenpw3'}, **HTMX
    )
    assert login.status_code == 200
    assert login['HX-Redirect'] != reverse('accounts:login')
    assert session_client.session['_auth_user_id'] == str(after.pk)
    # ... and the API's.
    api_login = Client().post(
        '/api/auth/login',
        {'email': victim, 'password': 'chosenpw3'},
        content_type='application/json',
    )
    assert api_login.status_code == 200
    assert api_login.json()['access_token']
    # The attacker's password, meanwhile, still gets nothing.
    refused = Client().post(
        '/api/auth/login',
        {'email': victim, 'password': 'attackerpw1'},
        content_type='application/json',
    )
    assert refused.status_code == 400


# -- rate limits --


def test_daily_ceiling_holds_after_the_hourly_window_passes(
    user, outbox, register, settings, monkeypatch
):
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {
        **settings.RATELIMIT_RULES,
        'SIGNUP_EXISTING_EMAIL': (1, 3600),
        'SIGNUP_EXISTING_EMAIL_DAY': (3, 24 * 3600),
        'SIGNUP_IP': (100, 3600),
    }
    clock = {'now': 24 * 3600 * 20_000.0}
    monkeypatch.setattr(limiter, 'time', SimpleNamespace(time=lambda: clock['now']))

    for _ in range(5):
        register(email=user.email)
        clock['now'] += 3600

    assert len(_notices(outbox, user.email)) == 3
    # Next day, the budget is back.
    clock['now'] = 24 * 3600 * 20_001.0
    register(email=user.email)
    assert len(_notices(outbox, user.email)) == 4


def test_a_skipped_email_costs_no_budget(user, outbox, register, settings, monkeypatch):
    """Both budgets are only counted when an email is actually queued: being turned away by the
    hourly one must not use up the daily one."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {
        **settings.RATELIMIT_RULES,
        'SIGNUP_EXISTING_EMAIL': (1, 3600),
        'SIGNUP_EXISTING_EMAIL_DAY': (2, 24 * 3600),
        'SIGNUP_IP': (100, 3600),
    }
    clock = {'now': 24 * 3600 * 20_000.0}
    monkeypatch.setattr(limiter, 'time', SimpleNamespace(time=lambda: clock['now']))

    for _ in range(10):  # all within one hour
        register(email=user.email)
    clock['now'] += 3600
    register(email=user.email)
    clock['now'] += 3600
    register(email=user.email)

    assert len(_notices(outbox, user.email)) == 2


def test_existing_address_submissions_still_spend_signup_ip(user, outbox, register, settings):
    """A probe isn't free: it counts against `SIGNUP_IP` like any signup, and over that budget
    nobody is emailed. Guards against `_create` being reordered so the notice runs first."""
    settings.RATELIMIT_ENABLE = True
    settings.RATELIMIT_RULES = {
        **settings.RATELIMIT_RULES,
        'SIGNUP_IP': (1, 3600),
        'SIGNUP_EXISTING_EMAIL': (5, 3600),
        'SIGNUP_EXISTING_EMAIL_DAY': (5, 24 * 3600),
    }

    first = register(email=user.email)
    second = register(email=user.email)

    assert first.status_code == 200
    assert second.status_code == 429
    assert len(_notices(outbox, user.email)) == 1


# -- timing: the same work either way --


def test_existing_address_hashes_the_submitted_password(user, outbox, register, monkeypatch):
    hashed: list[str] = []
    monkeypatch.setattr(
        existing_account, 'make_password', lambda password: hashed.append(password) or 'hash'
    )

    register(email=user.email, password='submitted123')
    _unconfirm(user)
    register(email=user.email, password='submitted456')

    assert hashed == ['submitted123', 'submitted456']


def test_both_paths_queue_one_email_instead_of_sending_inline(
    user, outbox, client, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=False) as new_callbacks:
        _register(client, email='brandnew@example.com')
    with django_capture_on_commit_callbacks(execute=False) as existing_callbacks:
        _register(Client(), email=user.email)

    # Nothing was sent while handling either request ...
    assert outbox == []
    # ... each left exactly one email to be queued once its transaction commits.
    assert len(new_callbacks) == len(existing_callbacks) == 1

    for callback in new_callbacks:
        callback()
    assert [m['subject'] for m in outbox] == [CONFIRMATION_SUBJECT]
    for callback in existing_callbacks:
        callback()
    assert [m['subject'] for m in outbox] == [CONFIRMATION_SUBJECT, SUBJECT]


def test_signup_confirmation_task_skips_a_moved_or_confirmed_account(
    outbox, django_capture_on_commit_callbacks
):
    from apps.accounts.services.email_confirmation import send_signup_confirmation_email

    account = User.objects.create_user(email='fresh@example.com', password='password123')

    send_signup_confirmation_email.call(str(account.pk), 'other@example.com')
    assert outbox == []

    User.objects.filter(pk=account.pk).update(email_confirmed=True)
    send_signup_confirmation_email.call(str(account.pk), 'fresh@example.com')
    assert outbox == []

    User.objects.filter(pk=account.pk).update(email_confirmed=False)
    send_signup_confirmation_email.call(str(account.pk), 'fresh@example.com')
    assert [m['to'] for m in outbox] == ['fresh@example.com']
