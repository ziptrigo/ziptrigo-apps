"""Email confirmation for accounts, built on the shared `core.services.email_verification`
(issue #58): `core` can't import `accounts` (see CLAUDE.md's layering), so the generic
start/confirm state machine lives there and this module only supplies the purpose, the
confirmation link, and the accounts-specific side effect (flipping `User.email_confirmed`).

This used to carry its own JWT (`EmailConfirmationToken`, now removed): a confirmation link was a
signed, stateless token that stayed valid until it expired -- clicking it twice, or an email
client's own link-prefetching, was always harmless. Moving onto `core`'s DB-backed, single-use
verifications changes that in one deliberate way: starting a *new* verification for the same
email (a resend) now invalidates the previous
one, so an old confirmation email left in someone's inbox stops working once a newer one has been
sent. `confirm_token` stays idempotent for a link that already confirmed *itself*, though (see
`apps.core.services.email_verification.confirm_by_token`), so a plain double-click still works
exactly like before -- only a *superseded* link's behaviour changes.

One more consequence worth calling out: this is a one-time migration cost. Any confirmation email
already sent before this change shipped was signed with the now-deleted `EmailConfirmationToken`
JWT class, so its link can no longer be validated at all (not "superseded", just unrecognised) --
those users need to use "resend confirmation" once. Judged low-impact: unconfirmed signups are
expected to be a small, transient population, and resending is one click already available on
every surface that shows an unconfirmed account.

Today's flow is link-only (no code-entry page exists), so this only ever calls `confirm_by_token`
-- `confirm_by_code` exists on the shared service for a future caller (`file_transfer`'s anonymous
sender, phase 2) that needs a code a user can type in without following a link. The confirmation
email doesn't mention a code either, to avoid advertising a feature this flow doesn't support.

Behaviour for existing users must not change, so link *validity* doesn't move onto the shared
`CoreSettings.email_verification_validity_minutes` default (30 minutes) either: this module passes
its historical `EMAIL_CONFIRMATION_TOKEN_TTL_HOURS` (48 hours) to `start(..., validity=...)`
explicitly, keeping the signup confirmation window exactly what it always was, independent of
whatever `CoreSettings` says for other purposes.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse

from apps.core.services.email import EmailBackendClass, get_email_backend
from apps.core.services.email_verification import (
    EmailVerificationContext,
    EmailVerificationError,
    EmailVerificationRateLimited,
    EmailVerificationSendFailed,
    ResendTooSoon,
    confirm_by_token,
    invalidate,
    start,
)
from apps.core.services.queue import enqueue_after_commit

from ..models import User
from ..signals import email_confirmed
from . import known_emails

logger = logging.getLogger(__name__)

#: `core.services.email_verification` scopes "only the latest verification is valid" and the
#: resend cooldown to `(email, purpose)` -- this string is accounts' own namespace within that, so
#: it can never collide with another app's purpose (e.g. `file_transfer`'s anonymous sender).
PURPOSE = 'accounts.email_confirmation'


@dataclass(slots=True)
class EmailConfirmationService:
    """High-level operations for the signup/email-change confirmation flow."""

    email_backend_classes: list[EmailBackendClass]

    def send_confirmation_email(self, user: User) -> None:
        """Start a new verification for `user.email` and send the confirmation email.

        Called from three places (the queued signup task `send_signup_confirmation_email`,
        `PUT /api/account` changing the email, and the `resend-confirmation` endpoint) -- none of
        which should turn either of the two expected,
        non-fatal outcomes below into a hard failure for the caller, so both are silently
        swallowed (after logging, for `EmailVerificationSendFailed`) rather than raised. This
        matches what the old JWT-based version did for a send failure: it called `send_email`
        directly and never checked the result, so a total delivery failure was already silent
        from the caller's perspective (signup, the email-change response and resend-confirmation
        all reported their usual success message regardless). The only behavioural difference
        `core.services.email_verification` introduces is that a *failed* send no longer
        invalidates whatever link was working before it (see `EmailVerificationSendFailed`'s
        docstring) -- strictly an improvement, not a user-visible change.

        - `ResendTooSoon`: a resend was requested before the shared cooldown elapsed.
        - `EmailVerificationRateLimited`: this address has hit its per-day cap on verification
          sends (issue #53) -- swallowed the same way, so a caller hammering resend can't turn
          this into a 500; the address simply stops getting new emails until the window resets.
        - `EmailVerificationSendFailed`: every configured email backend failed to deliver the
          new verification (already logged in detail by `apps.core.services.email.send_email`,
          one line per failed backend); logged here too, once, for this specific call's context.
        """

        def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
            return render_email_confirmation_email(
                user=user,
                confirmation_url=self._build_confirmation_url(context.token),
                validity_minutes=context.validity_minutes,
            )

        try:
            start(
                user.email,
                PURPOSE,
                build_email=build_email,
                email_backend_classes=self.email_backend_classes,
                validity=timedelta(hours=settings.EMAIL_CONFIRMATION_TOKEN_TTL_HOURS),
            )
        except ResendTooSoon:
            pass
        except EmailVerificationRateLimited:
            logger.warning('Verification email rate limit hit for %s', user.email)
        except EmailVerificationSendFailed:
            logger.warning('Confirmation email could not be delivered to %s', user.email)

    def resend_if_unconfirmed(self, email: str) -> None:
        """Queue a resend of the confirmation email for `email` if it belongs to an unconfirmed
        account; the task (`resend_confirmation_email`) does nothing otherwise -- including when no
        account has that address at all. Shared (issue #52) by the web `resend-confirmation` view
        and `POST /api/auth/resend-confirmation`, neither of which may reveal whether the address
        is registered (CLAUDE.md): the email is sent off the request for every address, known or
        not, so the response time says nothing either (issue #125).
        """
        enqueue_after_commit(resend_confirmation_email, email)

    def invalidate_pending_for_email(self, email: str) -> None:
        """Invalidate any still-pending confirmation for `email` (issue #58 follow-up): called
        right before a user's email changes away from it, so a still-valid confirmation link
        already sent to the address they're leaving can't later be used to confirm whatever
        account claims that address next -- see `apps.core.services.email_verification.invalidate`.
        """
        invalidate(email, PURPOSE)

    def _build_confirmation_url(self, token: str) -> str:
        base = settings.BASE_URL.rstrip('/')
        path = reverse('accounts:confirm-email', args=[token])
        return f'{base}{path}'

    @staticmethod
    def confirm_token(token: str) -> User | None:
        """Confirm the email address the token's verification was started for, and return the
        matching user -- or `None` if the token doesn't (or no longer) resolve to one. Callers
        treat that the same as an expired link, matching the old JWT behaviour (see module
        docstring): the token might be expired, superseded by a resend, unrecognised (e.g. a
        pre-migration JWT link), or -- new in issue #58 -- for a different purpose entirely.
        """
        try:
            email = confirm_by_token(token, PURPOSE)
        except EmailVerificationError:
            return None

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return None

        confirm_user(user)
        return user


def confirm_user(user: User) -> bool:
    """Mark `user`'s email confirmed, if it isn't already, and tell the apps that care (the
    `email_confirmed` signal: billing grants a promo code's credits when it fires). Returns whether
    anything changed. The one place that does it for a confirmation *link* and for an operator
    confirming an account by hand (`inv users confirm`), so the side effects can't drift."""
    if user.email_confirmed:
        return False
    user.email_confirmed = True
    user.email_confirmed_at = datetime.now(UTC)
    user.save(update_fields=['email_confirmed', 'email_confirmed_at'])
    send_email_confirmed(user)
    return True


def send_email_confirmed(user: User) -> None:
    """Tell the apps that care (`signals.email_confirmed`) that `user`'s address is now confirmed.
    Deferred to commit, so a receiver that writes (billing granting a promo code's credits) never
    sees a confirmation that is then rolled back; `send_robust`, so a failing receiver is logged
    and never turns the confirmation into an error page. The address is remembered as confirmed
    first (`known_emails.remember`, issue #160), in the confirming transaction, so a receiver sees
    who holds it."""
    known_emails.remember(user)

    def _send() -> None:
        for _, result in email_confirmed.send_robust(sender=User, user=user):
            if isinstance(result, Exception):
                logger.exception(
                    'email_confirmed receiver failed for user %s', user.pk, exc_info=result
                )

    transaction.on_commit(_send)


def get_email_confirmation_service() -> EmailConfirmationService:
    return EmailConfirmationService(email_backend_classes=get_email_backend())


@task
def send_signup_confirmation_email(user_id: str, email: str) -> None:
    """Send a new account's confirmation email, off the signup request (issue #125).

    Sending inline made a signup for a new address slower than one for an address that already has
    an account, which is a way to tell them apart; both now queue their email. Queued with
    `apps.core.services.queue.enqueue_after_commit` from `signup.create_account`; any failure here
    only logs -- `send_confirmation_email` already swallows a delivery failure or the resend
    cooldown, and the page's "resend confirmation" is the recovery for a mail that never came.

    `email` is the address the account was created with: if it has changed since, nothing is sent
    (the new address gets its own confirmation from `PUT /api/account`).
    """
    try:
        user = User.objects.get(pk=user_id, email=email)
    except User.DoesNotExist:
        return
    if user.email_confirmed:
        return
    get_email_confirmation_service().send_confirmation_email(user)


@task
def resend_confirmation_email(email: str) -> None:
    """Send a fresh confirmation email to the unconfirmed account with this address, if there is
    one; queued by `EmailConfirmationService.resend_if_unconfirmed` for every address submitted to
    the resend surfaces, so it looks the account up itself. The shared resend cooldown and the
    per-address daily cap still apply (`send_confirmation_email` swallows them)."""
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return
    if not user.email_confirmed:
        get_email_confirmation_service().send_confirmation_email(user)


def format_validity_minutes(minutes: int) -> str:
    """Render a validity window (in minutes) as friendly text, e.g. `'30 minutes'` or
    `'48 hours'`. Shared between the confirmation email and the "account created" page so both
    describe the same `EMAIL_CONFIRMATION_TOKEN_TTL_HOURS`-derived window the same way.
    """
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        return f'{hours} hour{"s" if hours != 1 else ""}'
    return f'{minutes} minute{"s" if minutes != 1 else ""}'


def render_email_confirmation_email(
    *, user: User, confirmation_url: str, validity_minutes: int
) -> tuple[str, str, str]:
    """Render email subject, text, and HTML body for a confirmation email.

    Returns:
        Tuple of (subject, text_body, html_body).
    """
    subject = 'Confirm your ZipTrigo account email'
    expiry_text = format_validity_minutes(validity_minutes)

    text_body = f"""Hi {user.name or user.email},

Please confirm the email address of your ZipTrigo account. Open the link below and press "Confirm my email":

{confirmation_url}

This link will expire in {expiry_text}.

If you did not expect this email, please ignore it.

--
The ZipTrigo Team
"""

    # Autoescaped: `user.name` is free text, and for an account whoever created it left
    # unconfirmed it is whatever *they* typed -- the address's owner may be the one reading it.
    html_body = render_to_string(
        'accounts/emails/email_confirmation.html',
        {
            'greeting_name': user.name or user.email,
            'confirmation_url': confirmation_url,
            'expiry_text': expiry_text,
        },
    )

    return subject, text_body, html_body
