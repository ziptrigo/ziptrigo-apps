"""The feedback emails print a time in the public wording, in UTC, whatever the format of the
person who sent the feedback or of the superuser who reads it (issue #245)."""

from datetime import UTC, datetime

import pytest

from apps.accounts.services.display_formats import set_display_formats
from apps.accounts.tests.factories import UserFactory

from ..models import Feedback
from ..services import submit_feedback

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def test_the_receipt_and_the_notification_use_the_public_wording(
    user, sent_emails, django_capture_on_commit_callbacks
):
    set_display_formats(user, date_format='dmy', time_format='24h')
    UserFactory(is_staff=True, is_superuser=True, date_format='dmy', time_format='24h')

    with django_capture_on_commit_callbacks(execute=True):
        feedback = submit_feedback(user, 'Nice site')
    Feedback.objects.filter(pk=feedback.pk).update(
        created_at=datetime(2026, 11, 5, 21, 5, tzinfo=UTC)
    )

    from ..services import emails

    emails.send_feedback_receipt.call(str(feedback.pk))
    emails.notify_superusers_of_feedback.call(str(feedback.pk))

    # The last of each: the ones sent by the calls above, after the time was fixed.
    receipt = [m for m in sent_emails if m['subject'] == 'Thanks for your feedback'][-1]
    notice = [m for m in sent_emails if m['subject'] == 'New feedback submitted'][-1]
    assert 'on Nov 5, 2026 at 9:05 PM UTC' in receipt['text_body']
    assert 'Submitted: Nov 5, 2026, 9:05 PM UTC' in notice['text_body']
    assert '05/11/2026' not in receipt['text_body'] and '21:05' not in notice['text_body']
