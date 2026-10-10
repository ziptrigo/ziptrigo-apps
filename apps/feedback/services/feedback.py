"""Submitting feedback (issue #82): validation plus the row and the two emails it triggers."""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User

from ..models import MAX_DESCRIPTION_LENGTH, Feedback
from . import emails


def validate_description(description: str) -> str:
    """Check the feedback text and return it normalized (surrounding whitespace stripped, line
    endings as `\\n` -- a browser submits `\\r\\n`, which would otherwise count twice against the
    cap and show up as stray carriage returns in the emails).

    Raises:
        ValidationError: If it's blank, or longer than `MAX_DESCRIPTION_LENGTH` characters.
    """
    description = description.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not description:
        raise ValidationError('Please write your feedback.')
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValidationError(
            f'Please keep your feedback to {MAX_DESCRIPTION_LENGTH:,} characters.'
        )
    return description


def submit_feedback(user: User, description: str) -> Feedback:
    """Save `user`'s feedback and queue the receipt to them and the notification to superusers.

    The emails are enqueued only once the row is committed (their tasks re-read it by id), so a
    rolled-back request can't leave an email about feedback that doesn't exist.

    Raises:
        ValidationError: See `validate_description`.
    """
    description = validate_description(description)
    feedback = Feedback.objects.create(created_by=user, description=description)
    feedback_id = str(feedback.id)
    transaction.on_commit(lambda: emails.send_feedback_receipt.enqueue(feedback_id))
    transaction.on_commit(lambda: emails.notify_superusers_of_feedback.enqueue(feedback_id))
    return feedback
