"""The "your email address was changed" notice, sent to the address an account is leaving.

`PUT /api/account` un-confirms the account and sends a confirmation to the *new* address; without
this, the owner of the old one would never hear about it. It goes only to an address that had been
confirmed (one the owner had proven), and it's a notice only: with the new address unconfirmed the
old owner can't use forgot-password (it goes to the new address) or a password login (it needs a
confirmed email), and there's no support address to point them at.

Like `password_change`, a `django.tasks` task with an autoescaped `html_body` (the greeting carries
the user's free-text name), queued with `enqueue_after_commit`.
"""

from datetime import UTC, datetime

from django.tasks import task
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core.services.email import send_email
from apps.core.services.queue import enqueue_after_commit

from ..models import User

SUBJECT = 'Your ZipTrigo email address was changed'


def notify_previous_address(user: User, previous_email: str) -> None:
    """Queue the notice to `previous_email`, which `user` has just moved away from."""
    enqueue_after_commit(
        send_email_changed_notice,
        str(user.pk),
        previous_email,
        user.email,
        timezone.now().isoformat(),
    )


@task
def send_email_changed_notice(
    user_id: str, previous_email: str, new_email: str, changed_at: str
) -> None:
    """ "Your email address was changed" -- to the address the account left."""
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    subject, text_body, html_body = render_email_changed_notice(
        user=user,
        previous_email=previous_email,
        new_email=new_email,
        changed_at=datetime.fromisoformat(changed_at),
    )
    send_email(to=previous_email, subject=subject, text_body=text_body, html_body=html_body)


def render_email_changed_notice(
    *, user: User, previous_email: str, new_email: str, changed_at: datetime
) -> tuple[str, str, str]:
    """Render the subject, text and HTML bodies of the "email changed" notice."""
    when = f'{changed_at.astimezone(UTC):%Y-%m-%d} at {changed_at.astimezone(UTC):%H:%M} UTC'
    greeting = f'Hi {user.name},' if user.name else 'Hi there,'

    text_body = (
        f'{greeting}\n\n'
        f'The email address on your ZipTrigo account was changed from {previous_email} to '
        f'{new_email} on {when}.\n\n'
        'If you made this change, you can ignore this email.\n\n'
        "If you didn't, someone else may have access to your account.\n\n"
        '--\n'
        'The ZipTrigo Team\n'
    )
    context = {
        'greeting': greeting,
        'previous_email': previous_email,
        'new_email': new_email,
        'when': when,
    }
    html_body = render_to_string('accounts/emails/email_changed.html', context)
    return SUBJECT, text_body, html_body
