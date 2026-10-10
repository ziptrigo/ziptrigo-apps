import pytest
from django.urls import reverse

from apps.accounts.tests.factories import UserFactory

from ..models import Feedback
from ..services import submit_feedback

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def _submit(user, description, capture):
    with capture(execute=True):
        return submit_feedback(user, description)


def test_submitter_gets_a_receipt_with_their_text(
    user, sent_emails, django_capture_on_commit_callbacks
):
    _submit(
        user, 'Love the file transfer.\nMore storage please.', django_capture_on_commit_callbacks
    )

    [receipt] = [m for m in sent_emails if m['to'] == user.email]
    assert receipt['subject'] == 'Thanks for your feedback'
    assert receipt['text_body'].startswith('Hi Test User,')
    assert '> Love the file transfer.\n> More storage please.' in receipt['text_body']
    assert 'UTC:' in receipt['text_body']
    assert 'Love the file transfer.' in receipt['html_body']
    assert 'The ZipTrigo team' in receipt['text_body']


def test_receipt_without_a_name_greets_generically(sent_emails, django_capture_on_commit_callbacks):
    nameless = UserFactory(name='', email_confirmed=True)

    _submit(nameless, 'Hi', django_capture_on_commit_callbacks)

    [receipt] = [m for m in sent_emails if m['to'] == nameless.email]
    assert receipt['text_body'].startswith('Hi there,')
    assert 'Hi there,' in receipt['html_body']


def test_unconfirmed_submitter_gets_no_receipt_but_superusers_are_still_notified(
    sent_emails, django_capture_on_commit_callbacks
):
    # `PUT /api/account` un-confirms the account on an email change but keeps the session, so the
    # text must not be mailed to an address nobody has proven they control.
    unconfirmed = UserFactory(email_confirmed=False)
    superuser = UserFactory(is_staff=True, is_superuser=True)

    _submit(unconfirmed, 'Please read this', django_capture_on_commit_callbacks)

    assert [m['to'] for m in sent_emails] == [superuser.email]
    assert all(m['subject'] != 'Thanks for your feedback' for m in sent_emails)


def test_each_active_superuser_gets_one_notification(
    user, sent_emails, django_capture_on_commit_callbacks
):
    first = UserFactory(is_staff=True, is_superuser=True)
    second = UserFactory(is_staff=True, is_superuser=True)
    UserFactory(is_staff=True)  # staff, but not a superuser
    UserFactory(is_staff=True, is_superuser=True, is_active=False)
    UserFactory(is_staff=True, is_superuser=True, status='INACTIVE')
    UserFactory(is_staff=True, is_superuser=True, status='DELETED')

    feedback = _submit(user, 'Needs dark mode', django_capture_on_commit_callbacks)

    notified = [m['to'] for m in sent_emails if m['subject'] == 'New feedback submitted']
    assert len(notified) == 2
    assert set(notified) == {first.email, second.email}
    notification = next(m for m in sent_emails if m['to'] == first.email)
    assert f'From: {user.name} ({user.email})' in notification['text_body']
    assert 'Status: New' in notification['text_body']
    assert '> Needs dark mode' in notification['text_body']
    change_url = reverse('custom_admin:feedback_feedback_change', args=[feedback.id])
    assert f'http://localhost:8000{change_url}' in notification['text_body']
    assert change_url in notification['html_body']


def test_no_user_text_in_any_subject(user, sent_emails, django_capture_on_commit_callbacks):
    UserFactory(is_staff=True, is_superuser=True)

    _submit(user, 'SECRET-MARKER', django_capture_on_commit_callbacks)

    assert sent_emails
    assert all('SECRET-MARKER' not in m['subject'] for m in sent_emails)


def test_html_in_feedback_is_escaped_in_both_html_bodies(
    user, sent_emails, django_capture_on_commit_callbacks
):
    UserFactory(is_staff=True, is_superuser=True)

    _submit(user, '<script>alert(1)</script>', django_capture_on_commit_callbacks)

    assert len(sent_emails) == 2
    for message in sent_emails:
        assert '<script>' not in message['html_body']
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in message['html_body']


def test_no_superusers_still_sends_the_receipt(
    user, sent_emails, django_capture_on_commit_callbacks, caplog
):
    _submit(user, 'Hello', django_capture_on_commit_callbacks)

    assert [m['to'] for m in sent_emails] == [user.email]
    assert 'no active superuser' in caplog.text


def test_nothing_is_sent_until_the_transaction_commits(user, sent_emails):
    submit_feedback(user, 'Hello')

    assert Feedback.objects.count() == 1
    assert sent_emails == []


def test_tasks_ignore_a_deleted_feedback(sent_emails):
    from ..services import emails

    emails.send_feedback_receipt.enqueue('00000000-0000-0000-0000-000000000000')
    emails.notify_superusers_of_feedback.enqueue('00000000-0000-0000-0000-000000000000')

    assert sent_emails == []
