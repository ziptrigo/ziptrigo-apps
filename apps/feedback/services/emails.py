"""The two feedback emails (issue #82), through `apps.core.services.email`.

Both are `django.tasks` tasks -- call `<name>.enqueue(...)`, never `<name>.call(...)` directly
(see `apps/file_transfer/services/emails.py`). Each re-reads the row by id and returns quietly if
it's gone.

Both pass an explicit `html_body` rendered from an autoescaped template: without one,
`SesEmailBackend` wraps `text_body` in `<pre>` *unescaped*, so feedback containing HTML would
render as HTML in the recipient's inbox. No feedback text goes into a subject line either.
"""

import logging
from datetime import UTC

from django.conf import settings
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.core.services.email import send_email

from ..models import Feedback

logger = logging.getLogger(__name__)

RECEIPT_SUBJECT = 'Thanks for your feedback'
NOTIFICATION_SUBJECT = 'New feedback submitted'


def _quote(text: str) -> str:
    return '\n'.join(f'> {line}' for line in text.split('\n'))


def _submitted_at(feedback: Feedback) -> tuple[str, str]:
    created_at = feedback.created_at.astimezone(UTC)
    return f'{created_at:%Y-%m-%d}', f'{created_at:%H:%M}'


def active_superusers():
    """Superusers who can actually receive the notification: active accounts with an address."""
    return User.objects.filter(
        is_superuser=True, is_active=True, status=User.STATUS_ACTIVE
    ).exclude(email='')


@task
def send_feedback_receipt(feedback_id: str) -> None:
    """ "Thanks for your feedback" -- to the submitter, with a copy of what they sent. Only sent to
    a confirmed address."""
    try:
        feedback = Feedback.objects.select_related('created_by').get(id=feedback_id)
    except Feedback.DoesNotExist:
        return
    user = feedback.created_by
    # Never mail the text to an address that hasn't been confirmed: `PUT /api/account` un-confirms
    # the account on an email change but leaves the session valid, so without this a user could
    # aim the receipt (their own text, plus their name) at someone else's inbox.
    if not user or not user.email or not user.email_confirmed:
        return

    date, time = _submitted_at(feedback)
    greeting = f'Hi {user.name},' if user.name else 'Hi there,'
    context = {
        'greeting': greeting,
        'date': date,
        'time': time,
        'description': feedback.description,
    }
    text_body = (
        f'{greeting}\n\n'
        'Thanks for taking the time to send us your feedback. Every message is read.\n\n'
        f"Here's a copy of what you sent on {date} at {time} UTC:\n\n"
        f'{_quote(feedback.description)}\n\n'
        'The ZipTrigo team'
    )
    send_email(
        to=user.email,
        subject=RECEIPT_SUBJECT,
        text_body=text_body,
        html_body=render_to_string('feedback/emails/receipt.html', context),
    )


@task
def notify_superusers_of_feedback(feedback_id: str) -> None:
    """ "New feedback submitted" -- to every active superuser, with the text and an admin link."""
    try:
        feedback = Feedback.objects.select_related('created_by').get(id=feedback_id)
    except Feedback.DoesNotExist:
        return

    recipients = list(active_superusers().values_list('email', flat=True))
    if not recipients:
        logger.warning(
            'Feedback %s was submitted but there is no active superuser to tell.', feedback_id
        )
        return

    user = feedback.created_by
    if user is None:
        sender = '(deleted user)'
    elif user.name:
        sender = f'{user.name} ({user.email})'
    else:
        sender = user.email
    date, time = _submitted_at(feedback)
    admin_url = settings.BASE_URL + reverse(
        'custom_admin:feedback_feedback_change', args=[feedback.id]
    )
    context = {
        'sender': sender,
        'submitted': f'{date} {time} UTC',
        'status': feedback.get_status_display(),
        'description': feedback.description,
        'admin_url': admin_url,
    }
    text_body = (
        f'From: {sender}\n'
        f'Submitted: {context["submitted"]}\n'
        f'Status: {context["status"]}\n\n'
        f'{_quote(feedback.description)}\n\n'
        f'Review it in the admin: {admin_url}'
    )
    html_body = render_to_string('feedback/emails/superuser_notification.html', context)
    for email in recipients:
        send_email(to=email, subject=NOTIFICATION_SUBJECT, text_body=text_body, html_body=html_body)
