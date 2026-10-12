"""What happens when someone signs up with an address that already has an account (issue #125).

The register page answers the same way for every valid submission (a redirect to "check your
email"), so it can't be used to learn whether an address has an account -- having one can itself be
private, and a confirmed list of registered addresses tells a phisher whom to target. Nothing is
created and no password changes; the address's owner is told by email instead, which is the only
channel an outsider can't read:

- **Confirmed account:** a "you already have an account" notice (`send_account_exists_email`),
  pointing at sign-in -- or "Sign in with Google" for an account that has no password to reset --
  and at forgot-password. Rate limited per address (`SIGNUP_EXISTING_EMAIL`, silently skipping the
  email when over it) so the form can't be used to flood someone's inbox.
- **Unconfirmed account:** the confirmation link is sent again instead. Whoever left that account
  unconfirmed most likely abandoned their own signup, and what they need next is the link, not
  directions to a sign-in that would refuse them ("confirm your email first"). It reuses the
  ordinary resend path, so it is bounded by the same resend cooldown and per-address daily cap as
  the resend-confirmation page, and no `SIGNUP_EXISTING_EMAIL` budget is spent on it.
- **Inactive or deleted account:** nothing. It can't sign in and mailing it directions to would
  mislead; the caller still gives the usual answer.

The caller treats this as best effort: `handle_signup_for_existing_email` never raises, so a
failure here (the email queue, the database) can't turn into an error page that only an
already-registered address would produce.
"""

import logging

from django.conf import settings
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse

from apps.core import ratelimit
from apps.core.services.email import send_email
from apps.core.services.queue import enqueue_after_commit

from ..models import User
from .email_confirmation import get_email_confirmation_service

logger = logging.getLogger(__name__)

SUBJECT = 'You already have a ZipTrigo account'


def handle_signup_for_existing_email(email: str) -> None:
    """Deal with a signup for `email`, which `create_account` found already registered: resend
    the confirmation link to an unconfirmed account, else queue the "you already have an account"
    notice to a confirmed one. Never raises (see module docstring)."""
    try:
        user = User.objects.filter(email=email).first()
        if user is None:
            # The row `create_account` collided with is gone again (or the collision was on
            # something else): nobody to tell.
            return
        if not user.is_active or user.status != User.STATUS_ACTIVE:
            return
        if not user.email_confirmed:
            get_email_confirmation_service().send_confirmation_email(user)
            return
        # Keyed on the lower-cased address, like the forgot-password rules, so `Foo@x.com` and
        # `foo@x.com` (two accounts as far as `User.email` goes) can't each get their own budget
        # at one person's inbox. Over the limit is silent: the page's answer must not change.
        if ratelimit.hit_value(email.lower(), 'SIGNUP_EXISTING_EMAIL').allowed:
            enqueue_after_commit(send_account_exists_email, str(user.pk), user.email)
    except Exception:
        logger.exception('Could not handle a signup for an already registered address')


@task
def send_account_exists_email(user_id: str, email: str) -> None:
    """ "You already have an account" -- to the existing account's address. `email` is the address
    the signup was for; if the account has moved to another one since it was queued, nothing is
    sent (the new address was never the subject of this signup)."""
    try:
        user = User.objects.get(pk=user_id, email=email)
    except User.DoesNotExist:
        return
    if not user.is_active or user.status != User.STATUS_ACTIVE or not user.email_confirmed:
        return

    subject, text_body, html_body = render_account_exists_email(user=user)
    send_email(to=user.email, subject=subject, text_body=text_body, html_body=html_body)


def render_account_exists_email(*, user: User) -> tuple[str, str, str]:
    """Render the subject, text and HTML bodies of the "you already have an account" email.

    Only the existing account's own data is used -- nothing from the signup form (the name typed
    there, say) -- so the form can't make this email carry text of an outsider's choosing.
    """
    base = settings.BASE_URL.rstrip('/')
    login_url = base + reverse('accounts:login')
    forgot_password_url = base + reverse('accounts:forgot-password')
    greeting = f'Hi {user.name},' if user.name else 'Hi there,'
    # An account made with Google has no password to remember or reset.
    google_only = bool(user.google_sub) and not user.has_usable_password()

    if google_only:
        how_to_sign_in = (
            'You signed up with Google. Use "Sign in with Google" on the sign-in page:\n\n'
            f'{login_url}\n\n'
        )
    else:
        how_to_sign_in = (
            f'You can sign in here:\n\n{login_url}\n\n'
            f'If you forgot your password, you can reset it here:\n\n{forgot_password_url}\n\n'
        )
    text_body = (
        f'{greeting}\n\n'
        f'Someone tried to create a ZipTrigo account with this email address ({user.email}), '
        'but you already have one.\n\n'
        'If it was you, there is nothing to set up again. '
        f'{how_to_sign_in}'
        "If it wasn't you, you can ignore this email: nothing about your account was changed.\n\n"
        '--\n'
        'The ZipTrigo Team\n'
    )
    context = {
        'greeting': greeting,
        'email': user.email,
        'google_only': google_only,
        'login_url': login_url,
        'forgot_password_url': forgot_password_url,
    }
    html_body = render_to_string('accounts/emails/account_exists.html', context)
    return SUBJECT, text_body, html_body
