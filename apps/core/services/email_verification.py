"""Generic email-address verification: a code and a link, shared by any app that needs to prove
someone controls an email address without necessarily having a user for it yet (issue #58).

`core` can't import `accounts` (see CLAUDE.md's layering), and an anonymous sender in
`file_transfer`'s later phase isn't a user at all, so this lives here keyed only on
`(email, purpose)` -- `purpose` is a free string the caller picks (e.g.
`'accounts.email_confirmation'`); `core` never interprets it. The email content and any link URL
are entirely the caller's responsibility too (`start`'s `build_email` callback) -- `core` doesn't
know `accounts`' or `file_transfer`'s URL names.

Security:
- The code and the token are generated with `secrets`, never stored in plaintext: only an
  HMAC-SHA256 digest (keyed with `settings.SECRET_KEY`) is persisted (`EmailVerification.code_hash`
  / `.token_hash`), and a user-supplied value is compared against it with `hmac.compare_digest`
  (constant-time), never `==`.
- Both are single-use: a successful confirm sets `confirmed_at`. `confirm_by_code` then rejects
  any further attempt against that row outright (`EmailVerificationAlreadyConfirmed`).
  `confirm_by_token` is deliberately the exception: it's idempotent for a row *that same token*
  already confirmed, returning the same email again rather than raising -- see its docstring for
  why (it's load-bearing for the `accounts` port in this same issue).
- A wrong code counts as an attempt; once `attempts` reaches the configured max the row is
  "burned" (`EmailVerificationBurned`) even for the right code from then on. `confirm_by_token`
  is not subject to this: a token isn't guessable, so there's nothing to burn it against, and
  burning it anyway would let a code-guessing attacker lock the real recipient out of their own
  link.
- Starting a new verification for an `(email, purpose)` that already has a pending one invalidates
  the previous row (`invalidated_at`) -- only the newest is ever valid
  (`EmailVerificationSuperseded` if an old one is used later).

Settings (code length, validity, max attempts, resend cooldown) come from the admin-editable
`CoreSettings` singleton (`/admin/core/coresettings/`), read fresh on every call -- except
validity, which `start`'s caller may override per call (`validity: timedelta`) when a purpose
needs a window the shared default shouldn't dictate (e.g. `accounts` keeping signup
confirmation's historical 48-hour lifetime independent of the shared default other purposes use).
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import CoreSettings, EmailVerification
from .email import EmailBackendClass, get_email_backend, send_email

_DIGITS = '0123456789'


class EmailVerificationError(Exception):
    """Base class for every error `start`/`confirm_by_code`/`confirm_by_token` can raise."""


class EmailVerificationNotFound(EmailVerificationError):
    """No verification matches the given id/token (wrong purpose, typo, or never existed)."""


class EmailVerificationExpired(EmailVerificationError):
    """The verification's validity window has passed."""


class EmailVerificationBurned(EmailVerificationError):
    """Too many wrong codes were entered; the row is dead until a resend."""


class EmailVerificationSuperseded(EmailVerificationError):
    """A newer verification was started for the same email and purpose; only it is valid."""


class EmailVerificationAlreadyConfirmed(EmailVerificationError):
    """This verification was already used by code. Strictly single-use, unlike the token path --
    see the module docstring."""


class IncorrectCode(EmailVerificationError):
    """The code didn't match. Carries how many attempts are left before the row burns."""

    def __init__(self, attempts_remaining: int):
        self.attempts_remaining = attempts_remaining
        super().__init__(f'Incorrect code ({attempts_remaining} attempt(s) remaining).')


class ResendTooSoon(EmailVerificationError):
    """A verification for this email+purpose was started too recently. Carries the wait left."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f'Resend too soon; retry after {retry_after_seconds}s.')


@dataclass(frozen=True, slots=True)
class EmailVerificationContext:
    """What a caller's `build_email` callback gets to build its subject/body/link from."""

    email: str
    purpose: str
    code: str
    token: str
    expires_at: datetime
    validity_minutes: int


type BuildEmail = Callable[[EmailVerificationContext], tuple[str, str, str]]


def _digest(value: str) -> str:
    """HMAC-SHA256 hex digest of `value`, keyed with `SECRET_KEY`."""
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), sha256).hexdigest()


def _matches(candidate: str, digest: str) -> bool:
    """Constant-time comparison of a user-supplied value against a stored digest."""
    return hmac.compare_digest(_digest(candidate), digest)


def _generate_code(length: int) -> str:
    return ''.join(secrets.choice(_DIGITS) for _ in range(length))


def start(
    email: str,
    purpose: str,
    *,
    build_email: BuildEmail,
    email_backend_classes: list[EmailBackendClass] | None = None,
    validity: timedelta | None = None,
) -> uuid.UUID:
    """Start a new verification for `email`/`purpose`: generate a code and a token, email them
    (`build_email` builds the subject/text/html -- and any confirmation link, from
    `context.token` -- which is then sent via `apps.core.services.email.send_email`), and return
    the new row's id.

    `validity` overrides `CoreSettings.email_verification_validity_minutes` for this call only.
    Core stays generic -- it doesn't know or care why a caller wants a different window, only
    that one purpose's needs shouldn't force every other purpose onto the same number. (Used by
    `accounts`: signup confirmation keeps its historical 48-hour link lifetime,
    `EMAIL_CONFIRMATION_TOKEN_TTL_HOURS`, independent of the shared default other purposes use.)

    Raises `ResendTooSoon` (carrying the remaining wait, in seconds) if a verification for this
    `email`/`purpose` was already started within `CoreSettings.email_verification_resend_cooldown_seconds`.
    Otherwise, invalidates any previous still-pending verification for the same `email`/`purpose`
    first -- only the newest one is ever valid.
    """
    settings_row = CoreSettings.load()
    now = timezone.now()

    latest = (
        EmailVerification.objects.filter(email=email, purpose=purpose)
        .order_by('-created_at')
        .first()
    )
    if latest is not None:
        cooldown = timedelta(seconds=settings_row.email_verification_resend_cooldown_seconds)
        elapsed = now - latest.created_at
        if elapsed < cooldown:
            retry_after = cooldown - elapsed
            raise ResendTooSoon(retry_after_seconds=int(retry_after.total_seconds()) + 1)

    code = _generate_code(settings_row.email_verification_code_length)
    token = secrets.token_urlsafe(32)
    effective_validity = (
        validity
        if validity is not None
        else timedelta(minutes=settings_row.email_verification_validity_minutes)
    )
    validity_minutes = int(effective_validity.total_seconds() // 60)
    expires_at = now + effective_validity

    with transaction.atomic():
        EmailVerification.objects.filter(
            email=email,
            purpose=purpose,
            confirmed_at__isnull=True,
            invalidated_at__isnull=True,
        ).update(invalidated_at=now)

        verification = EmailVerification.objects.create(
            email=email,
            purpose=purpose,
            code_hash=_digest(code),
            token_hash=_digest(token),
            expires_at=expires_at,
        )

    context = EmailVerificationContext(
        email=email,
        purpose=purpose,
        code=code,
        token=token,
        expires_at=expires_at,
        validity_minutes=validity_minutes,
    )
    subject, text_body, html_body = build_email(context)
    send_email(
        to=email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        backend_classes=email_backend_classes or get_email_backend(),
    )

    return verification.id


def confirm_by_code(verification_id: uuid.UUID | str, code: str) -> str:
    """Confirm a pending verification by its id (as returned by `start`) and the code the user
    typed. Returns the verified email, or raises one of this module's exceptions.

    Strictly single-use: confirming an already-confirmed row raises
    `EmailVerificationAlreadyConfirmed` rather than silently succeeding again -- unlike
    `confirm_by_token`, there's no legacy behaviour here that depends on idempotent resubmission.
    """
    try:
        verification = EmailVerification.objects.get(pk=verification_id)
    except EmailVerification.DoesNotExist, ValueError, TypeError, ValidationError:
        raise EmailVerificationNotFound from None

    if verification.confirmed_at is not None:
        raise EmailVerificationAlreadyConfirmed
    if verification.invalidated_at is not None:
        raise EmailVerificationSuperseded
    if timezone.now() >= verification.expires_at:
        raise EmailVerificationExpired

    settings_row = CoreSettings.load()
    max_attempts = settings_row.email_verification_max_attempts

    if verification.attempts >= max_attempts:
        raise EmailVerificationBurned

    if not _matches(code, verification.code_hash):
        verification.attempts += 1
        verification.save(update_fields=['attempts'])
        if verification.attempts >= max_attempts:
            raise EmailVerificationBurned
        raise IncorrectCode(attempts_remaining=max_attempts - verification.attempts)

    verification.confirmed_at = timezone.now()
    verification.save(update_fields=['confirmed_at'])
    return verification.email


def confirm_by_token(token: str) -> str:
    """Confirm a pending verification by the token from its link. Returns the verified email, or
    raises one of this module's exceptions.

    Idempotent when the very same token already confirmed this row: clicking a confirmation link
    twice (a browser/email-client link-prefetch, or a user genuinely double-clicking) just
    returns the same email again rather than raising. This matters for `accounts`' port onto this
    service (issue #58): its old JWT-based confirmation links were never single-use -- they only
    ever *expired* -- and this keeps that behaviour unchanged. Attempt-burning never applies to
    the token path at all: unlike a code, a token isn't guessable, so there's nothing to protect
    by burning it, and doing so would let a code-guessing attacker lock the real recipient out of
    their own link.
    """
    try:
        verification = EmailVerification.objects.get(token_hash=_digest(token))
    except EmailVerification.DoesNotExist:
        raise EmailVerificationNotFound from None

    if verification.confirmed_at is not None:
        return verification.email

    if verification.invalidated_at is not None:
        raise EmailVerificationSuperseded
    if timezone.now() >= verification.expires_at:
        raise EmailVerificationExpired

    verification.confirmed_at = timezone.now()
    verification.save(update_fields=['confirmed_at'])
    return verification.email


def purge_old(older_than: timedelta = timedelta(days=30)) -> int:
    """Delete verification rows older than `older_than` (by `created_at`). By then they're long
    past their (much shorter) validity window regardless of outcome, so nothing keeps needing
    them beyond occasional support/debugging use, which the admin's read-only list still serves
    for anything more recent. Returns how many were deleted. Used by `apps.core.jobs`."""
    cutoff = timezone.now() - older_than
    deleted, _ = EmailVerification.objects.filter(created_at__lt=cutoff).delete()
    return deleted
