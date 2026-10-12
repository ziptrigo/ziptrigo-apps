"""Changing a password from the account page, and the "your password was changed" email it sends.

The email is a `django.tasks` task -- call `.enqueue(...)`, never `.call(...)` directly (see
`apps/file_transfer/services/emails.py`) -- enqueued only once the new password is committed. It
tells the owner when it happened and, if it wasn't them, to reset the password through the
forgot-password page.

It passes an explicit `html_body` rendered from an autoescaped template: without one,
`SesEmailBackend` wraps `text_body` in an unescaped `<pre>`, and the greeting carries the user's
own (free-text) name.
"""

from datetime import UTC, datetime

from django.conf import settings
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.core.services.email import send_email
from apps.core.services.queue import enqueue_after_commit

from ..models import User

SUBJECT = 'Your ZipTrigo password was changed'


def change_password(user: User, new_password: str) -> None:
    """Set `user`'s new password and queue the notification email once it's committed (a
    failure to queue it is logged, never raised: the new password is already saved by then).

    The caller has already checked the current password and validated the new one
    (`apps.accounts.forms.ChangePasswordForm`). A session view must also call
    `update_session_auth_hash` afterwards, or the change logs out the very session that made it.
    """
    user.set_password(new_password)
    user.save(update_fields=['password'])
    enqueue_after_commit(send_password_changed_email, str(user.pk), timezone.now().isoformat())


@task
def send_password_changed_email(user_id: str, changed_at: str) -> None:
    """ "Your password was changed" -- to the account's address. Only sent to a confirmed
    address."""
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return
    # Never mail an address that hasn't been confirmed: `PUT /api/account` un-confirms the account
    # on an email change but leaves the session valid, so without this a user could aim this email
    # (with their free-text name in it) at someone else's inbox.
    if not user.email or not user.email_confirmed:
        return

    subject, text_body, html_body = render_password_changed_email(
        user=user, changed_at=datetime.fromisoformat(changed_at)
    )
    send_email(to=user.email, subject=subject, text_body=text_body, html_body=html_body)


def render_password_changed_email(*, user: User, changed_at: datetime) -> tuple[str, str, str]:
    """Render the subject, text and HTML bodies of the "password changed" email."""
    when = f'{changed_at.astimezone(UTC):%Y-%m-%d} at {changed_at.astimezone(UTC):%H:%M} UTC'
    forgot_password_url = settings.BASE_URL.rstrip('/') + reverse('accounts:forgot-password')
    greeting = f'Hi {user.name},' if user.name else 'Hi there,'

    text_body = (
        f'{greeting}\n\n'
        f'The password for your ZipTrigo account ({user.email}) was changed on {when}.\n\n'
        'If you made this change, you can ignore this email.\n\n'
        "If you didn't, someone else may have access to your account. Reset your password now:\n\n"
        f'{forgot_password_url}\n\n'
        '--\n'
        'The ZipTrigo Team\n'
    )
    context = {
        'greeting': greeting,
        'email': user.email,
        'when': when,
        'forgot_password_url': forgot_password_url,
    }
    html_body = render_to_string('accounts/emails/password_changed.html', context)
    return SUBJECT, text_body, html_body
