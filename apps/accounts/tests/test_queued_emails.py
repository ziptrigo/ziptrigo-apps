"""Forgot-password and resend-confirmation send their email off the request (issue #125 follow-up).

Sending inline only for an address that has an account made the response time say which addresses
do, and held a sync worker for the duration of an SES call. Both surfaces (the web views and
`/api/auth/...`) now queue exactly one task per submission, for a known and an unknown address
alike; the task looks the account up when it runs.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.services.email_confirmation import resend_confirmation_email
from apps.accounts.services.password_reset import send_password_reset_email

HTMX = {'HTTP_HX_REQUEST': 'true'}

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    messages: list[dict] = []

    def fake_send_email(**kwargs):
        messages.append(kwargs)
        return 1, 0

    monkeypatch.setattr('apps.accounts.services.password_reset.send_email', fake_send_email)
    monkeypatch.setattr('apps.core.services.email_verification.send_email', fake_send_email)
    return messages


@pytest.fixture
def unconfirmed(user):
    user.email_confirmed = False
    user.email_confirmed_at = None
    user.save()
    return user


def _web_forgot(client, email):
    return client.post(reverse('accounts:forgot-password'), {'email': email}, **HTMX)


def _api_forgot(client, email):
    return client.post(
        '/api/auth/forgot-password', {'email': email}, content_type='application/json'
    )


def _web_resend(client, email):
    return client.post(reverse('accounts:resend-confirmation'), {'email': email}, **HTMX)


def _api_resend(client, email):
    return client.post(
        '/api/auth/resend-confirmation', {'email': email}, content_type='application/json'
    )


FORGOT = [_web_forgot, _api_forgot]
RESEND = [_web_resend, _api_resend]


# -- Forgot password --


@pytest.mark.parametrize('submit', FORGOT)
def test_forgot_password_sends_nothing_inline(
    submit, client, user, sent, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = submit(client, user.email)

    assert response.status_code == 200
    assert sent == []  # not in the request
    assert len(callbacks) == 1  # one queued task

    for callback in callbacks:
        callback()

    [message] = sent
    assert message['to'] == user.email
    assert 'reset-password' in message['text_body']


@pytest.mark.parametrize('submit', FORGOT)
def test_forgot_password_queues_the_same_for_an_unknown_address(
    submit, client, sent, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = submit(client, 'nobody@example.com')

    assert response.status_code == 200
    assert len(callbacks) == 1  # exactly what a known address queues
    assert sent == []  # and the task finds no account to mail


@pytest.mark.parametrize('submit', FORGOT)
def test_forgot_password_does_the_same_database_work_for_a_known_and_an_unknown_address(
    submit, client, user
):
    known = _queries(lambda: submit(client, user.email))
    unknown = _queries(lambda: submit(client, 'nobody@example.com'))

    assert known == unknown


def test_the_reset_task_skips_an_address_the_account_has_left(user, sent):
    old_email = user.email
    user.email = 'moved@example.com'
    user.save()

    send_password_reset_email.call(old_email)

    assert sent == []


def test_the_reset_task_sends_a_link_bound_to_the_address(user, sent):
    send_password_reset_email.call(user.email)

    [message] = sent
    assert message['to'] == user.email


# -- Resend confirmation --


@pytest.mark.parametrize('submit', RESEND)
def test_resend_confirmation_sends_nothing_inline(
    submit, client, unconfirmed, sent, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = submit(client, unconfirmed.email)

    assert response.status_code == 200
    assert sent == []
    assert len(callbacks) == 1

    for callback in callbacks:
        callback()

    [message] = sent
    assert message['to'] == unconfirmed.email


@pytest.mark.parametrize('submit', RESEND)
@pytest.mark.parametrize('address', ['nobody@example.com', 'testuser@example.com'])
def test_resend_confirmation_queues_the_same_for_unknown_and_confirmed_addresses(
    submit, address, client, user, sent, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = submit(client, address)

    assert response.status_code == 200
    assert len(callbacks) == 1
    assert sent == []  # no account, or one that is already confirmed


@pytest.mark.parametrize('submit', RESEND)
def test_resend_confirmation_does_the_same_database_work_for_a_known_and_an_unknown_address(
    submit, client, unconfirmed
):
    known = _queries(lambda: submit(client, unconfirmed.email))
    unknown = _queries(lambda: submit(client, 'nobody@example.com'))

    assert known == unknown


def test_the_resend_task_skips_an_address_the_account_has_left(unconfirmed, sent):
    old_email = unconfirmed.email
    unconfirmed.email = 'moved@example.com'
    unconfirmed.save()

    resend_confirmation_email.call(old_email)

    assert sent == []


def test_the_resend_task_skips_an_account_confirmed_meanwhile(unconfirmed, sent):
    User.objects.filter(pk=unconfirmed.pk).update(email_confirmed=True)

    resend_confirmation_email.call(unconfirmed.email)

    assert sent == []


def _queries(action) -> int:
    """How many queries `action` ran. Equal counts for a known and an unknown address mean the
    request does not branch on whether the account exists (the work that differs is in the task)."""
    with CaptureQueriesContext(connection) as captured:
        action()
    return len(captured)
