"""The account page's "Change Password" form and the "password changed" email."""

from datetime import UTC, datetime

import pytest
from django.db import transaction
from django.urls import reverse

from apps.accounts.services.password_change import (
    SUBJECT,
    change_password,
    render_password_changed_email,
    send_password_changed_email,
)

pytestmark = [pytest.mark.django_db, pytest.mark.unit]

HTMX = {'HTTP_HX_REQUEST': 'true'}
PASSWORD = 'testpass123'
NEW_PASSWORD = 'newpass456'


@pytest.fixture()
def sent_emails(monkeypatch):
    """Capture what the email task hands to `send_email` instead of sending anything."""
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.accounts.services.password_change.send_email', lambda **kwargs: sent.append(kwargs)
    )
    return sent


def _change(client, current=PASSWORD, new=NEW_PASSWORD, confirm=None, htmx=True):
    data = {
        'current_password': current,
        'new_password': new,
        'new_password_confirm': new if confirm is None else confirm,
    }
    extra = HTMX if htmx else {}
    return client.post(reverse('accounts:password-change'), data, **extra)


def test_account_page_renders_working_password_form(client, user):
    client.force_login(user)

    content = client.get(reverse('accounts:account')).content.decode()

    assert f'hx-post="{reverse("accounts:password-change")}"' in content
    assert 'coming soon' not in content
    assert 'name="current_password"' in content


def test_changes_password_and_keeps_session(
    client, user, sent_emails, django_capture_on_commit_callbacks
):
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _change(client)

    assert response.status_code == 200
    content = response.content.decode()
    assert 'Password changed.' in content
    assert '<html' not in content
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    # `update_session_auth_hash` kept this session signed in.
    assert client.get(reverse('accounts:account')).status_code == 200


def test_signs_out_other_sessions(client, user, django_capture_on_commit_callbacks):
    from django.test import Client

    other = Client()
    other.force_login(user)
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        _change(client)

    response = other.get(reverse('accounts:account'))
    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))


def test_sends_notification_email(client, user, sent_emails, django_capture_on_commit_callbacks):
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        _change(client)

    assert len(sent_emails) == 1
    email = sent_emails[0]
    assert email['to'] == user.email
    assert email['subject'] == SUBJECT
    assert '/account/forgot-password/' in email['text_body']
    assert '/account/forgot-password/' in email['html_body']


def test_wrong_current_password(client, user, sent_emails, django_capture_on_commit_callbacks):
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _change(client, current='wrongpass1')

    assert response.status_code == 422
    assert 'Your current password is incorrect.' in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
    assert sent_emails == []


@pytest.mark.parametrize(
    ('new', 'confirm', 'message'),
    [
        ('abc12', None, 'Password must be at least 6 characters long.'),
        ('abcdefg', None, 'Password must contain at least one digit.'),
        (NEW_PASSWORD, 'other456', 'Passwords do not match.'),
        (PASSWORD, None, 'The new password must be different from the current one.'),
        # Django's `AUTH_PASSWORD_VALIDATORS`, on top of `PasswordValidator`.
        ('password1', None, 'This password is too common.'),
        ('testuser1', None, 'The password is too similar to the email.'),
    ],
)
def test_invalid_new_password(client, user, new, confirm, message):
    client.force_login(user)

    response = _change(client, new=new, confirm=confirm)

    assert response.status_code == 422
    assert message in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_without_htmx_redirects_to_account_page(client, user, django_capture_on_commit_callbacks):
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _change(client, htmx=False)

    assert response.status_code == 302
    assert response['Location'] == reverse('accounts:account')


def test_without_htmx_errors_render_full_page(client, user):
    client.force_login(user)

    response = _change(client, current='wrongpass1', htmx=False)

    assert response.status_code == 422
    content = response.content.decode()
    assert '<html' in content
    assert 'Your current password is incorrect.' in content


def test_requires_login(client):
    response = _change(client, htmx=False)

    assert response.status_code == 302
    assert response['Location'].startswith(reverse('accounts:login'))


def test_requires_login_htmx_navigates_to_the_login_page(client):
    response = _change(client)

    assert response['HX-Redirect'].startswith(reverse('accounts:login'))


def test_get_not_allowed(client, user):
    client.force_login(user)

    assert client.get(reverse('accounts:password-change')).status_code == 405


@pytest.mark.parametrize('htmx', [True, False])
def test_account_without_password_is_redirected(client, user, htmx):
    user.set_unusable_password()
    user.save()
    client.force_login(user)

    response = _change(client, current='', htmx=htmx)

    if htmx:
        assert response['HX-Redirect'] == reverse('accounts:account')
    else:
        assert response.status_code == 302
        assert response['Location'] == reverse('accounts:account')
    user.refresh_from_db()
    assert not user.has_usable_password()


def test_rolled_back_change_sends_no_email(user, sent_emails, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError), transaction.atomic():
            change_password(user, NEW_PASSWORD)
            raise RuntimeError('roll back')

    assert callbacks == []
    assert sent_emails == []


def test_queue_failure_does_not_fail_the_change(
    client, user, monkeypatch, caplog, django_capture_on_commit_callbacks
):
    class BrokenTask:
        name = 'send_password_changed_email'

        def enqueue(self, *args):
            raise RuntimeError('queue is down')

    monkeypatch.setattr(
        'apps.accounts.services.password_change.send_password_changed_email', BrokenTask()
    )
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _change(client)

    assert response.status_code == 200
    assert 'Password changed.' in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert 'Could not queue send_password_changed_email' in caplog.text


class TestRateLimit:
    @pytest.fixture(autouse=True)
    def _enable(self, settings):
        settings.RATELIMIT_ENABLE = True
        settings.RATELIMIT_RULES = {**settings.RATELIMIT_RULES, 'CHANGE_PASSWORD_USER': (2, 300)}

    def test_429_after_the_limit(self, client, user):
        client.force_login(user)
        for _ in range(2):
            assert _change(client, current='wrongpass1').status_code == 422

        response = _change(client)

        assert response.status_code == 429
        assert 'Retry-After' in response
        assert 'Too many attempts' in response.content.decode()
        assert '<html' not in response.content.decode()
        user.refresh_from_db()
        assert user.check_password(PASSWORD)

    def test_every_attempt_counts(self, client, user):
        """Counted before the current password is checked, so a burst of parallel requests can't
        all slip past a "check first, record failures later" counter."""
        client.force_login(user)
        assert _change(client, confirm='mismatch1').status_code == 422
        assert _change(client, new='abcdefg').status_code == 422

        assert _change(client).status_code == 429

    def test_429_without_htmx_renders_the_full_page(self, client, user):
        client.force_login(user)
        for _ in range(2):
            _change(client, current='wrongpass1', htmx=False)

        response = _change(client, htmx=False)

        assert response.status_code == 429
        assert 'Retry-After' in response
        content = response.content.decode()
        assert '<html' in content
        assert 'Too many attempts' in content


class TestEmail:
    def test_not_sent_to_unconfirmed_address(self, user, sent_emails):
        user.email_confirmed = False
        user.save()

        send_password_changed_email.call(str(user.pk), datetime.now(UTC).isoformat())

        assert sent_emails == []

    def test_missing_user_is_ignored(self, sent_emails):
        send_password_changed_email.call(
            '00000000-0000-0000-0000-000000000000', datetime.now(UTC).isoformat()
        )

        assert sent_emails == []

    def test_render(self, user, settings):
        settings.BASE_URL = 'https://app.example.com'
        changed_at = datetime(2026, 10, 12, 14, 5, tzinfo=UTC)

        subject, text_body, html_body = render_password_changed_email(
            user=user, changed_at=changed_at
        )

        assert subject == SUBJECT
        url = 'https://app.example.com/account/forgot-password/'
        for body in (text_body, html_body):
            assert 'Hi Test User,' in body
            assert user.email in body
            assert '2026-10-12 at 14:05 UTC' in body
            assert url in body
        assert f'href="{url}"' in html_body

    def test_html_escapes_the_name(self, user):
        user.name = '<b>Mallory</b>'

        _, _, html_body = render_password_changed_email(user=user, changed_at=datetime.now(UTC))

        assert '<b>Mallory</b>' not in html_body
        assert '&lt;b&gt;Mallory&lt;/b&gt;' in html_body
