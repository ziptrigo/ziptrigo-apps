"""What happens when someone signs up with an address that already has an account (issue #125).

The register page answers the same way for every valid submission (a redirect to "check your
email"), so it can't be used to learn whether an address has an account -- having one can itself be
private, and a confirmed list of registered addresses tells a phisher whom to target. Nothing is
created and the visitor's name and password are never stored; the address's owner is told by email
instead, which is the only channel an outsider can't read:

- **Confirmed account:** a "you already have an account" notice (`send_account_exists_email`),
  pointing at sign-in -- or "Sign in with Google" for an account that has no password to reset --
  and at forgot-password. Nothing about the account changes.
- **Unconfirmed account:** the confirmation link is deliberately *not* sent again. Whoever left the
  account unconfirmed chose its password, and that may be someone other than the address's owner
  (a "pre-hijack": register a victim's address with a password you know, wait for the victim to
  confirm it). A link that confirmed *that* account would hand it to its creator. So the account is
  locked down instead (`_lock_down_unconfirmed`): its password is made unusable, so whatever it was
  can never be confirmed into a working login, and every pending confirmation link for the address
  is invalidated; then the owner is mailed a "finish setting up your account" link
  (`send_finish_setup_email`) that lets them choose a password. Following it confirms the email
  (any password reset does, see `password_reset`). The email carries nothing from the account row
  (no name: on an unconfirmed account it is whatever its creator typed) or from the form. Locking
  down happens on every attempt, even when the email is rate limited; if the owner never gets the
  email, forgot-password works for an unconfirmed account too and does the same.
- **Inactive or deleted account** (and an unconfirmed account linked to a Google identity, which
  only an email change can produce, or a staff account, which an admin made): nothing. It can't
  sign in, and mailing it directions would mislead or let a signup take over (or lock) an account
  someone is using.

Both emails share one per-address budget (`SIGNUP_EXISTING_EMAIL` hourly, `SIGNUP_EXISTING_EMAIL_DAY`
daily; over it the email is silently skipped) so the form can't be used to flood an inbox.

Timing: the new-account path hashes the password and queues its confirmation email; this one hashes
the submitted password too (and discards it) and queues its own email, so the two take roughly as
long and response time doesn't give an address away either. The password is never stored or logged.

The caller treats this as best effort: `handle_signup_for_existing_email` never raises, so a
failure here (the email queue, the database) can't turn into an error page that only an
already-registered address would produce.
"""

import logging

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse

from apps.core import ratelimit
from apps.core.services.email import send_email
from apps.core.services.queue import enqueue_after_commit

from ..models import User
from .email_confirmation import get_email_confirmation_service
from .password_reset import get_password_reset_service

logger = logging.getLogger(__name__)

SUBJECT = 'You already have a ZipTrigo account'


FINISH_SETUP_SUBJECT = 'Finish setting up your ZipTrigo account'


def handle_signup_for_existing_email(email: str, password: str) -> None:
    """Deal with a signup for `email`, which `create_account` found already registered: queue the
    "you already have an account" notice to a confirmed account, or lock down an unconfirmed one
    and queue the "finish setting up" email to its owner. Never raises (see module docstring).

    `password` is what the visitor typed; it is only hashed (to spend the time a new account's
    hash would have) and never stored or logged.
    """
    user_pk = None
    try:
        make_password(password)
        user = User.objects.filter(email=email).first()
        if user is None:
            # The row `create_account` collided with is gone again (or the collision was on
            # something else): nobody to tell.
            return
        user_pk = user.pk
        if not user.is_active or user.status != User.STATUS_ACTIVE:
            return
        if user.email_confirmed:
            if _may_email(email):
                enqueue_after_commit(send_account_exists_email, str(user.pk), user.email)
            return
        if user.google_sub or user.is_staff or user.is_superuser:
            # Not the creator-chose-the-password case this guards against: a staff account is made
            # by an admin or `createsuperuser` (which leaves the email unconfirmed), and locking
            # its password on a stranger's say-so would lock the admin out.
            return
        _lock_down_unconfirmed(user)
        if _may_email(email):
            enqueue_after_commit(send_finish_setup_email, str(user.pk), user.email)
    except Exception:
        # The user's id, never the address or anything the visitor typed.
        logger.exception(
            'Could not handle a signup for an already registered address (user %s)', user_pk
        )


def _lock_down_unconfirmed(user: User) -> None:
    """Make sure nothing the creator of an unconfirmed account set up can be confirmed into a
    working login: its password becomes unusable (which also revokes every JWT and session issued
    under it, `tokens.password_fingerprint`) and every pending confirmation link for the address
    is invalidated, so a link sent earlier can't confirm the account either. Name and email are
    left alone. Idempotent."""
    with transaction.atomic():
        if user.has_usable_password():
            user.set_unusable_password()
            user.save(update_fields=['password'])
        get_email_confirmation_service().invalidate_pending_for_email(user.email)


def _may_email(email: str) -> bool:
    """Whether an email for `email` may be queued now, and if so spend its budget: the hourly
    (`SIGNUP_EXISTING_EMAIL`) and daily (`SIGNUP_EXISTING_EMAIL_DAY`) rules are both checked first,
    and both counted only when it will actually go out. Keyed on the lower-cased address, like the
    forgot-password rules, so `Foo@x.com` and `foo@x.com` (two accounts as far as `User.email`
    goes) can't each get their own budget at one person's inbox. Over the limit is silent: the
    page's answer must not change."""
    key = email.lower()
    rules = ('SIGNUP_EXISTING_EMAIL', 'SIGNUP_EXISTING_EMAIL_DAY')
    if not all(ratelimit.peek_value(key, rule).allowed for rule in rules):
        return False
    # Counted in this order and all of them, so a concurrent request that took the last slot
    # between the peek and here still stops this one.
    return all([ratelimit.hit_value(key, rule).allowed for rule in rules])


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


@task
def send_finish_setup_email(user_id: str, email: str) -> None:
    """ "Finish setting up your account" -- to the address of an unconfirmed account that someone
    tried to sign up with. `email` is that address; if the account has moved to another one since
    it was queued, or has been confirmed, nothing is sent. The link is made here, not when it was
    queued, so it is as fresh as the email."""
    try:
        user = User.objects.get(pk=user_id, email=email)
    except User.DoesNotExist:
        return
    if not user.is_active or user.status != User.STATUS_ACTIVE or user.email_confirmed:
        return

    subject, text_body, html_body = render_finish_setup_email(
        email=user.email, set_password_url=get_password_reset_service().reset_url_for(user)
    )
    send_email(to=user.email, subject=subject, text_body=text_body, html_body=html_body)


def render_finish_setup_email(*, email: str, set_password_url: str) -> tuple[str, str, str]:
    """Render the subject, text and HTML bodies of the "finish setting up" email.

    Takes the address and the link only -- deliberately not the user: the name on an unconfirmed
    account is whatever its creator typed, and nothing from the signup form is used either.
    """
    ttl_hours = settings.PASSWORD_RESET_TOKEN_TTL_HOURS
    text_body = (
        'Hi there,\n\n'
        f'Someone (maybe you) started creating a ZipTrigo account with this email address ({email}).\n\n'
        'To finish setting it up, choose a password here:\n\n'
        f'{set_password_url}\n\n'
        f'This link will expire in {ttl_hours} hours.\n\n'
        "If it wasn't you, you can ignore this email.\n\n"
        '--\n'
        'The ZipTrigo Team\n'
    )
    context = {'email': email, 'set_password_url': set_password_url, 'ttl_hours': ttl_hours}
    html_body = render_to_string('accounts/emails/finish_setup.html', context)
    return FINISH_SETUP_SUBJECT, text_body, html_body
