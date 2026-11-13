"""Sign in with an emailed one-time code or link (issue #295), built on the shared
`core.services.email_verification`, like email confirmation is.

One email carries both a code and a link; either signs the person in (a Django session, started by
the view). The purpose is `accounts.login_otp`, so a token or code minted for an email
confirmation can never sign anyone in, and the reverse.

- **Who gets one.** An active account (`is_active` and `status == ACTIVE`) whose email is
  **confirmed**. Passwords don't matter: an account with no usable password (Google or Microsoft
  sign-in) gets one like any other; staff and superusers too (the admin's own login is separate).
  An *unconfirmed* account gets nothing: whoever created it chose its password, and may not be the
  address's owner (the "pre-hijack" in CLAUDE.md). Emailing the real owner a way into an account
  an attacker made would put them in it, with the attacker holding its password. The owner's way
  in is the "finish setting up" email or forgot-password, which replace that password.
- **No enumeration.** `request_code` queues `send_login_code_email` for every submitted address,
  known or not; the task looks the account up and sends nothing when there is nothing to send, so
  the request does the same work and answers the same for every address. Checking a code answers
  one generic failure whatever went wrong (no such account, no code started, wrong, expired,
  burned, already used, account not eligible).
- **Codes.** `email_verification.start` generates a code (the shared length, `CoreSettings`) and
  a token for a window of `settings.LOGIN_OTP_VALIDITY_MINUTES`; only hashes are stored; a newer
  code supersedes the older ones; the shared resend cooldown and per-address daily cap apply
  (swallowed: the request has already been answered). A code is checked against the *newest*
  verification for the address (`confirm_latest_by_code`), since the queued start means the
  request never learned a row id. It is strictly single use, and a wrong code costs one of
  `max_attempts`, after which the row is burned. **Trade-off, accepted:** a stranger can burn or
  supersede someone's pending code (a few wrong guesses, or another request after the cooldown);
  the owner just asks for a new one, and the rate limits bound how often. It never locks the
  account: the password and the other ways in are untouched.
- **The link** is a token that opens a page with a button; only the POST signs in
  (`views.login_code.login_link_page`), so a mail scanner's GET consumes nothing. It is strictly
  single use too (`confirm_by_token(..., single_use=True)`): using the link burns the code and
  using the code burns the link, since they are one row.
- **No side effects on the account.** Signing in this way never changes the password, never
  confirms an email (only confirmed accounts get here) and never writes `google_sub` or
  `microsoft_id`.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest
from django.tasks import task
from django.template.loader import render_to_string
from django.urls import reverse

from apps.core import ratelimit
from apps.core.ratelimit import RateLimitResult
from apps.core.services.email_verification import (
    EmailVerificationContext,
    EmailVerificationError,
    EmailVerificationRateLimited,
    EmailVerificationSendFailed,
    ResendTooSoon,
    confirm_by_token,
    confirm_latest_by_code,
    start,
)
from apps.core.services.notifications import is_safe_url
from apps.core.services.queue import enqueue_after_commit

from ..models import User
from .email_confirmation import format_validity_minutes

logger = logging.getLogger(__name__)

#: `core.services.email_verification`'s purpose for this flow (see the module docstring).
PURPOSE = 'accounts.login_otp'

#: Longest code the form accepts: `CoreSettings.email_verification_code_length` is at most 10.
CODE_MAX_LENGTH = 12

_CODE_SEPARATORS = re.compile(r'[\s-]+')


def can_sign_in_with_code(user: User) -> bool:
    """Whether `user` may be sent a sign-in code and signed in with one (see the module
    docstring for why an unconfirmed account may not)."""
    return bool(user.is_active and user.status == User.STATUS_ACTIVE and user.email_confirmed)


def clean_code(raw: str) -> str:
    """The code as typed, without the spaces and hyphens people add when copying `123 456`."""
    return _CODE_SEPARATORS.sub('', raw or '')


def request_code(email: str, next_url: str | None = None) -> None:
    """Queue a sign-in email for the account with this address, if there is an eligible one: the
    task does nothing otherwise. Queued for every submitted address, known or not, so the request
    does the same work either way (see the module docstring). `next_url` is carried into the
    emailed link only when it is a same-site path."""
    enqueue_after_commit(
        send_login_code_email, email, next_url if is_safe_url(next_url or '') else ''
    )


@task
def send_login_code_email(email: str, next_url: str = '') -> None:
    """Start a verification and email the code and link to the eligible account with this address;
    nothing for an unknown, inactive or unconfirmed one. The shared cooldown, the daily cap and a
    failed send are swallowed (the page already answered, and asking again is the recovery)."""
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return
    if not can_sign_in_with_code(user):
        return

    def build_email(context: EmailVerificationContext) -> tuple[str, str, str]:
        return render_login_code_email(
            user=user,
            code=context.code,
            sign_in_url=build_sign_in_url(context.token, next_url),
            validity_minutes=context.validity_minutes,
        )

    try:
        start(
            user.email,
            PURPOSE,
            build_email=build_email,
            validity=timedelta(minutes=settings.LOGIN_OTP_VALIDITY_MINUTES),
        )
    except ResendTooSoon:
        pass
    except EmailVerificationRateLimited:
        logger.warning('Sign-in code rate limit hit for user %s', user.pk)
    except EmailVerificationSendFailed:
        logger.warning('Sign-in code could not be delivered to user %s', user.pk)


def build_sign_in_url(token: str, next_url: str = '') -> str:
    """The absolute link in the email; the page it opens only shows a button."""
    url = settings.BASE_URL.rstrip('/') + reverse('accounts:login-link', args=[token])
    if next_url and is_safe_url(next_url):
        url += '?' + urlencode({'next': next_url})
    return url


def check_before_verify(request: HttpRequest, email: str) -> RateLimitResult:
    """Call before checking a typed code for `email` (lower-cased). Charges the strict
    `LOGIN_OTP_VERIFY_IP` and per-(address, IP) `LOGIN_OTP_VERIFY_EMAIL_IP` on every attempt, then
    only peeks at the looser per-address `LOGIN_OTP_VERIFY_EMAIL`, which counts failures alone
    (`record_failed_verify`) -- the shape of `login_throttle`, for the same reason: right codes
    never spend the budget a wrong guess from elsewhere is limited by. Returns the result that
    blocks, or the allowed peek."""
    limited = ratelimit.hit_ip(request, 'LOGIN_OTP_VERIFY_IP')
    if limited.allowed:
        limited = ratelimit.hit_ip_and_value(request, email, 'LOGIN_OTP_VERIFY_EMAIL_IP')
    if limited.allowed:
        limited = ratelimit.peek_value(email, 'LOGIN_OTP_VERIFY_EMAIL')
    return limited


def record_failed_verify(email: str) -> None:
    """Count one failed code for `email` (lower-cased) against `LOGIN_OTP_VERIFY_EMAIL`. Failures
    for an address nobody has an account for count the same, so the limit reveals nothing."""
    ratelimit.hit_value(email, 'LOGIN_OTP_VERIFY_EMAIL')


def check_before_request(request: HttpRequest, email: str) -> RateLimitResult:
    """Call before queueing a sign-in email for `email` (lower-cased): `LOGIN_OTP_REQUEST_IP`,
    then the strict per-(address, IP) and looser per-address budgets, each counted on every
    request whatever the address is, so a 429 says nothing about the account."""
    limited = ratelimit.hit_ip(request, 'LOGIN_OTP_REQUEST_IP')
    if limited.allowed:
        limited = ratelimit.hit_ip_and_value(request, email, 'LOGIN_OTP_REQUEST_EMAIL_IP')
    if limited.allowed:
        limited = ratelimit.hit_value(email, 'LOGIN_OTP_REQUEST_EMAIL')
    return limited


def user_for_code(email: str, code: str) -> User | None:
    """The user the newest sign-in code for `email` signs in, or `None` for every reason a code
    can fail (no code started, wrong, expired, burned, replaced, used) and when the account is no
    longer eligible. A correct code is consumed even then. A wrong one costs an attempt."""
    try:
        verified_email = confirm_latest_by_code(email, clean_code(code), PURPOSE)
    except EmailVerificationError:
        return None
    return _eligible_user(verified_email)


def user_for_token(token: str) -> User | None:
    """The user the sign-in link's token signs in, or `None` (see `user_for_code`)."""
    try:
        verified_email = confirm_by_token(token, PURPOSE, single_use=True)
    except EmailVerificationError:
        return None
    return _eligible_user(verified_email)


def _eligible_user(email: str) -> User | None:
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return None
    return user if can_sign_in_with_code(user) else None


def render_login_code_email(
    *, user: User, code: str, sign_in_url: str, validity_minutes: int
) -> tuple[str, str, str]:
    """Subject, text and HTML bodies. The subject is fixed (and has no code in it: it shows on lock
    screens); the HTML is autoescaped, `user.name` being free text."""
    subject = 'Your ZipTrigo sign-in code'
    expiry_text = format_validity_minutes(validity_minutes)
    greeting_name = user.name or user.email

    text_body = f"""Hi {greeting_name},

Use this code to sign in to ZipTrigo:

{code}

Or open the link below and press "Sign in":

{sign_in_url}

The code and the link work once and expire in {expiry_text}.

If you did not ask to sign in, you can ignore this email: nobody can sign in without it, and your password has not changed.

--
The ZipTrigo Team
"""
    html_body = render_to_string(
        'accounts/emails/login_code.html',
        {
            'greeting_name': greeting_name,
            'code': code,
            'sign_in_url': sign_in_url,
            'expiry_text': expiry_text,
        },
    )
    return subject, text_body, html_body
