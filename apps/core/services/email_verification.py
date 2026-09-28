"""Generic email-address verification: a code and a link, shared by any app that needs to prove
someone controls an email address without necessarily having a user for it yet (issue #58).

`core` can't import `accounts` (see CLAUDE.md's layering), and an anonymous sender in
`file_transfer`'s later phase isn't a user at all, so this lives here keyed only on
`(email, purpose)` -- `purpose` is a free string the caller picks (e.g.
`'accounts.email_confirmation'`); `core` never interprets it beyond scoping rows to it, and every
caller-facing entry point (`confirm_by_code`, `confirm_by_token`) requires it explicitly, so a
token or code minted for one purpose can never confirm a row that belongs to another. The email
content and any link URL are entirely the caller's responsibility too (`start`'s `build_email`
callback) -- `core` doesn't know `accounts`' or `file_transfer`'s URL names.

Security:
- The code and the token are generated with `secrets`, never stored in plaintext: only an
  HMAC-SHA256 digest (keyed with `settings.SECRET_KEY`) is persisted (`EmailVerification.code_hash`
  / `.token_hash`), and a user-supplied value is compared against it with `hmac.compare_digest`
  (constant-time), never `==`.
- Both are single-use: a successful confirm sets `confirmed_at`. `confirm_by_code` then rejects
  any further attempt against that row outright (`EmailVerificationAlreadyConfirmed`).
  `confirm_by_token` is deliberately the exception: it's idempotent for a row *that same token*
  already confirmed *while still within its validity window* -- see its docstring for why (it's
  load-bearing for the `accounts` port in this same issue).
- A wrong code counts as an attempt; once `attempts` reaches the configured max the row is
  "burned" (`EmailVerificationBurned`) even for the right code from then on. `confirm_by_token`
  is not subject to this: a token isn't guessable, so there's nothing to burn it against, and
  burning it anyway would let a code-guessing attacker lock the real recipient out of their own
  link.
- Starting a new verification for an `(email, purpose)` that already has a pending one invalidates
  the previous row (`invalidated_at`) -- only the newest is ever valid
  (`EmailVerificationSuperseded` if an old one is used later). `invalidate` does the same thing
  on demand, for a caller that needs to retire pending rows for a reason other than a resend (e.g.
  `accounts` invalidating a pending verification for an email a user is moving away from).
- Concurrency: `start`, `confirm_by_code` and `confirm_by_token` each run their read-check-write
  sequence inside `transaction.atomic()` with `select_for_update()` on the row(s) involved, which
  actually serialises concurrent callers on Postgres (prod's database; a no-op on SQLite, since
  `select_for_update` silently degrades to a plain `SELECT` there -- see
  `django.db.backends.base.features.BaseDatabaseFeatures.has_select_for_update`). Independently of
  that lock, the two writes that matter most for correctness under a race --
  `confirm_by_code`'s attempt increment and both confirm functions' `confirmed_at` -- are only
  ever applied through a conditional `UPDATE` keyed on the exact value just read (`attempts=<n>`,
  `confirmed_at__isnull=True`), never a blind `instance.save()`. That makes them safe even on a
  backend without real row locking: if some other request already changed the row between this
  request's read and its write, the conditional update matches 0 rows and this request re-reads
  reality instead of trusting a stale snapshot -- see `confirm_by_code`'s and `confirm_by_token`'s
  docstrings for what each does with that outcome, and
  `apps/core/tests/test_email_verification_service.py` for tests that simulate the race
  deterministically (no threads needed) by using `timezone.now()` as a hook to run a
  "concurrent" call in between a request's read and its write.
- Email matching (the resend cooldown, "only the newest is valid", and `invalidate`) is
  case-insensitive (`email__iexact`), so `Foo@x.com` and `foo@x.com` share the same cooldown and
  the same "latest row" -- but the `email` column itself is stored and returned exactly as the
  caller passed it in, unchanged. That matters for `accounts`: `User.email` is only
  domain-lowercased by Django's `normalize_email` (not the local part), so a user with a
  mixed-case stored email still round-trips through `start` -> `confirm_by_token` ->
  `User.objects.get(email=...)` using the exact same string, with no risk of a normalised
  version failing to match their actual row.

Settings (code length, validity, max attempts, resend cooldown) come from the admin-editable
`CoreSettings` singleton (`/admin/core/coresettings/`), read fresh on every call -- except
validity, which `start`'s caller may override per call (`validity: timedelta`) when a purpose
needs a window the shared default shouldn't dictate (e.g. `accounts` keeping signup
confirmation's historical 48-hour lifetime independent of the shared default other purposes use).

Rate limiting (issue #53): `start` also enforces a per-email-address-per-day cap
(`settings.RATELIMIT_RULES['EMAIL_VERIFICATION_START_EMAIL']`, via `apps.core.ratelimit`) within
each caller-supplied `rate_limit_group` (default: `purpose` -- issue #53 code review: separate
groups so one app's flows can't exhaust another's budget), raising `EmailVerificationRateLimited`
-- see that exception's docstring and `start`'s own for why this lives here rather than in each
caller.
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
from django.db.models import F
from django.utils import timezone

from ..models import CoreSettings, EmailVerification
from ..ratelimit import hit_value
from .email import EmailBackendClass, get_email_backend, send_email

_DIGITS = '0123456789'


class EmailVerificationError(Exception):
    """Base class for every error `start`/`confirm_by_code`/`confirm_by_token` can raise."""


class EmailVerificationNotFound(EmailVerificationError):
    """No verification matches the given id/token *and* purpose (wrong purpose, typo, or never
    existed -- these are deliberately indistinguishable from the caller's perspective)."""


class EmailVerificationExpired(EmailVerificationError):
    """The verification's validity window has passed."""


class EmailVerificationBurned(EmailVerificationError):
    """Too many wrong codes were entered (or a concurrent request raced this one and won); the
    row is dead until a resend."""


class EmailVerificationSuperseded(EmailVerificationError):
    """A newer verification was started for the same email and purpose; only it is valid."""


class EmailVerificationAlreadyConfirmed(EmailVerificationError):
    """This verification was already used by code (by this request or a concurrent one that won
    the race). Strictly single-use, unlike the token path -- see the module docstring."""


class EmailVerificationSendFailed(EmailVerificationError):
    """`start` generated and stored a new verification, but every configured email backend
    failed to deliver it. The whole operation is rolled back -- as if `start` had never been
    called -- so callers may safely retry or surface this without worrying about a dangling,
    undelivered row or an already-started resend cooldown."""

    def __init__(self, failures: int):
        self.failures = failures
        super().__init__(f'Failed to send verification email ({failures} backend(s) failed).')


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


class EmailVerificationRateLimited(EmailVerificationError):
    """This `email` has had `settings.RATELIMIT_RULES['EMAIL_VERIFICATION_START_EMAIL']` starts
    already today within `start`'s `rate_limit_group` (issue #53; see the module docstring's
    "Rate limiting" section). Carries the wait left, like `ResendTooSoon`."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f'Too many verification emails sent; retry after {retry_after_seconds}s.')


@dataclass(frozen=True, slots=True)
class EmailVerificationContext:
    """What a caller's `build_email` callback gets to build its subject/body/link from."""

    email: str
    purpose: str
    code: str
    token: str
    expires_at: datetime
    validity_minutes: int


@dataclass(frozen=True, slots=True)
class EmailVerificationResult:
    """What `confirm_by_token_verbose` returns: the verified email *and* which row confirmed it.

    A caller that hands out a link from a URL that itself names some other object (e.g.
    `file_transfer`'s `/send/anon/<draft_id>/confirm/link/<token>/`) needs the second part: `email`
    alone only proves *an* address was confirmed, not that this particular token was ever minted
    for *that* object -- matching emails is not enough to rule out a token swapped in from a
    different but same-addressed object's own confirmation link. Comparing `verification_id`
    against whatever id the caller stored when it called `start` closes that gap.
    """

    email: str
    verification_id: uuid.UUID


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
    rate_limit_group: str | None = None,
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

    Raises `EmailVerificationRateLimited` if `email` has already had
    `settings.RATELIMIT_RULES['EMAIL_VERIFICATION_START_EMAIL']` starts today *within
    `rate_limit_group`* (default: `purpose` itself) -- the resend cooldown above only limits how
    *fast* a fresh code can be requested, not how many times in a day, and it's scoped per
    `(email, purpose)` -- a caller whose `purpose` varies per object (`file_transfer`'s anonymous
    sender, one purpose per transfer) gets no cross-object cooldown at all from that alone.
    `rate_limit_group` is how a caller like that opts back in: passing the same group string for
    every object's own distinct `purpose` (`file_transfer` passes its one shared
    `anon_emails.PURPOSE` for every transfer's own `verification_purpose(transfer_id)`) makes the
    cap apply across all of them, closing the cross-transfer gap, while `accounts`' single,
    already-shared purpose needs no override at all (issue #53 / issue #53 code review: separate
    groups also mean `accounts`' account-lifecycle emails and `file_transfer`'s anonymous-send
    emails draw from independent budgets and can't starve each other). Checked *after* the
    cooldown above, and only once a resend actually would generate a new code/link -- a call
    rejected by the cooldown never reaches this check, so hammering the same still-cooling-down
    `(email, purpose)` can't burn through the daily quota without ever producing a usable code.
    Skipped entirely (like every other rate limit) when `RATELIMIT_ENABLE` is `False`.

    Raises `EmailVerificationSendFailed` if every configured email backend fails to deliver the
    new verification -- and rolls back everything this call would otherwise have changed (the new
    row, and the invalidation of the previous one), so a total send failure never silently
    invalidates a still-working link or starts a cooldown for an email nobody received.

    The cooldown check and the invalidate-then-create sequence run inside one
    `transaction.atomic()` block, locking the latest existing row for this `email`/`purpose`
    (`select_for_update`, see the module docstring) so two truly concurrent `start` calls
    serialise rather than both reading a cooldown-clear state and both sending. That lock has
    nothing to hold when there is no previous row yet (a genuinely first-ever `start` for this
    `email`/`purpose`) -- two of *those* racing is not closed by this and would need a
    database-level uniqueness constraint or an advisory lock to close entirely; left as a known,
    narrow gap (accounts' own `User.email` uniqueness already rules it out for that caller).
    """
    settings_row = CoreSettings.load()
    now = timezone.now()

    with transaction.atomic():
        latest = (
            EmailVerification.objects.select_for_update()
            .filter(email__iexact=email, purpose=purpose)
            .order_by('-created_at')
            .first()
        )
        if latest is not None:
            cooldown = timedelta(seconds=settings_row.email_verification_resend_cooldown_seconds)
            elapsed = now - latest.created_at
            if elapsed < cooldown:
                retry_after = cooldown - elapsed
                raise ResendTooSoon(retry_after_seconds=int(retry_after.total_seconds()) + 1)

        group = rate_limit_group if rate_limit_group is not None else purpose
        limit_result = hit_value(f'{group}:{email.lower()}', 'EMAIL_VERIFICATION_START_EMAIL')
        if not limit_result.allowed:
            raise EmailVerificationRateLimited(retry_after_seconds=limit_result.retry_after)

        code = _generate_code(settings_row.email_verification_code_length)
        token = secrets.token_urlsafe(32)
        effective_validity = (
            validity
            if validity is not None
            else timedelta(minutes=settings_row.email_verification_validity_minutes)
        )
        validity_minutes = int(effective_validity.total_seconds() // 60)
        expires_at = now + effective_validity

        EmailVerification.objects.filter(
            email__iexact=email,
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
        successes, failures = send_email(
            to=email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            backend_classes=email_backend_classes or get_email_backend(),
        )
        if successes == 0:
            # Raising here rolls back the whole `atomic()` block: the invalidation above and the
            # `create()` are both undone, so this call really did nothing.
            raise EmailVerificationSendFailed(failures=failures)

        return verification.id


def invalidate(email: str, purpose: str) -> int:
    """Invalidate every still-pending (not confirmed, not already invalidated) verification for
    `email`/`purpose`, the same way starting a new one would. For a caller that needs to retire
    pending rows for a reason other than a resend -- e.g. `accounts` calling this for a user's
    *old* email address right when it changes, so a still-valid confirmation link sent to that
    address can't later confirm whatever account claims it next (issue #58 follow-up). Matches
    case-insensitively, like `start`'s own lookups. Returns how many rows were invalidated.
    """
    return EmailVerification.objects.filter(
        email__iexact=email,
        purpose=purpose,
        confirmed_at__isnull=True,
        invalidated_at__isnull=True,
    ).update(invalidated_at=timezone.now())


def confirm_by_code(verification_id: uuid.UUID | str, code: str, purpose: str) -> str:
    """Confirm a pending verification by its id (as returned by `start`), the code the user
    typed, and the `purpose` it was started for. Returns the verified email, or raises one of
    this module's exceptions.

    `purpose` must match the row's own `purpose` -- checked as part of the lookup itself, so a
    mismatch raises the same `EmailVerificationNotFound` as an unknown id, never revealing that a
    row exists for a different purpose.

    Strictly single-use: confirming an already-confirmed row raises
    `EmailVerificationAlreadyConfirmed` rather than silently succeeding again -- unlike
    `confirm_by_token`, there's no legacy behaviour here that depends on idempotent resubmission.

    Concurrency: the whole check runs inside `transaction.atomic()` with `select_for_update()` on
    the row (see the module docstring). No exception is ever raised from inside that block --
    every outcome is decided as plain local variables first, and the block always exits
    normally, so a decision this call makes about *its own* row can never be undone by a
    rollback of work a nested, "concurrent" call already committed within the same block (this
    matters for how `apps/core/tests/test_email_verification_service.py` simulates a race: two
    calls sharing one DB connection, one triggered from inside the other's own execution, are
    only truly independent -- one committing while the other later raises -- if raising never
    rolls back anything past its own call). Both possible writes -- the attempt increment on a
    wrong code, and setting `confirmed_at` on a right one -- go through a conditional `UPDATE`
    keyed on the exact value just read, never a blind `instance.save()`:

    - Wrong code: `UPDATE ... SET attempts = attempts + 1 WHERE id = ... AND attempts = <n>`. If a
      concurrent request already changed `attempts` since this request read it, 0 rows match and
      this guess is rejected as `EmailVerificationBurned` -- conservative, but correct: a stale
      guess must never be allowed to silently succeed *or* to blindly overwrite the counter with
      a value that would undercount how many guesses were actually made (the bypass this closes:
      without this guard, any number of guesses made in parallel from the same stale `attempts`
      snapshot each computed and wrote back the same `snapshot + 1`, so a burst of guesses only
      ever cost the counter "1", regardless of burst size).
    - Right code: `UPDATE ... SET confirmed_at = now() WHERE id = ... AND confirmed_at IS NULL`.
      If a concurrent request already confirmed the row (right code or otherwise) between this
      request's read and this write, 0 rows match and this call raises
      `EmailVerificationAlreadyConfirmed` instead of also reporting success -- two correct
      submissions can never both succeed.
    """
    result: str | None = None
    error: EmailVerificationError | None = None

    with transaction.atomic():
        try:
            verification = EmailVerification.objects.select_for_update().get(
                pk=verification_id, purpose=purpose
            )
        except EmailVerification.DoesNotExist, ValueError, TypeError, ValidationError:
            error = EmailVerificationNotFound()
        else:
            if verification.confirmed_at is not None:
                error = EmailVerificationAlreadyConfirmed()
            elif verification.invalidated_at is not None:
                error = EmailVerificationSuperseded()
            elif timezone.now() >= verification.expires_at:
                error = EmailVerificationExpired()
            else:
                settings_row = CoreSettings.load()
                max_attempts = settings_row.email_verification_max_attempts

                if verification.attempts >= max_attempts:
                    error = EmailVerificationBurned()
                elif _matches(code, verification.code_hash):
                    updated = EmailVerification.objects.filter(
                        pk=verification.pk, confirmed_at__isnull=True
                    ).update(confirmed_at=timezone.now())
                    if updated == 0:
                        # A concurrent request confirmed (or invalidated) this row between our
                        # read and our write. Whichever it was, this request's own attempt must
                        # not also report success.
                        error = EmailVerificationAlreadyConfirmed()
                    else:
                        result = verification.email
                else:
                    attempts_before = verification.attempts
                    updated = EmailVerification.objects.filter(
                        pk=verification.pk, attempts=attempts_before
                    ).update(attempts=F('attempts') + 1)
                    if updated == 0:
                        # Some other request already advanced `attempts` (or
                        # confirmed/invalidated the row) since we read it -- our snapshot is
                        # stale, and we wrote nothing. Fail closed rather than trust it: see the
                        # docstring above for why this must never silently succeed.
                        error = EmailVerificationBurned()
                    else:
                        new_attempts = attempts_before + 1
                        if new_attempts >= max_attempts:
                            error = EmailVerificationBurned()
                        else:
                            error = IncorrectCode(attempts_remaining=max_attempts - new_attempts)

    if error is not None:
        raise error
    assert result is not None
    return result


def confirm_by_token(token: str, purpose: str) -> str:
    """Thin, string-returning wrapper around `confirm_by_token_verbose` for callers (`accounts`)
    that only ever need the email back -- see that function for the full contract and every
    exception this can raise."""
    return confirm_by_token_verbose(token, purpose).email


def confirm_by_token_verbose(token: str, purpose: str) -> EmailVerificationResult:
    """Confirm a pending verification by the token from its link and the `purpose` it was
    started for. Returns the verified email *and* the row's own id (see
    `EmailVerificationResult`), or raises one of this module's exceptions.

    `purpose` must match the row's own `purpose` -- checked as part of the lookup itself, so a
    mismatch raises the same `EmailVerificationNotFound` as an unknown token.

    Idempotent when the very same token already confirmed this row *and* the row is still within
    its validity window: clicking a confirmation link twice (a browser/email-client
    link-prefetch, or a user genuinely double-clicking) just returns the same email again rather
    than raising. This matters for `accounts`' port onto this service (issue #58): its old
    JWT-based confirmation links were never single-use -- they only ever *expired* -- and this
    keeps that behaviour unchanged, including the part where the link eventually stops working:
    past `expires_at`, an already-confirmed row now raises `EmailVerificationExpired` too, rather
    than reporting success forever (the old JWT would have failed to validate by then). Attempt-
    burning never applies to the token path at all: unlike a code, a token isn't guessable, so
    there's nothing to protect by burning it, and doing so would let a code-guessing attacker
    lock the real recipient out of their own link.

    Concurrency: like `confirm_by_code`, the whole check runs inside `transaction.atomic()` with
    `select_for_update()` on the row, and (also like `confirm_by_code`) no exception is ever
    raised from inside that block -- see its docstring for why. Setting `confirmed_at` goes
    through the same kind of conditional `UPDATE` (`WHERE id = ... AND confirmed_at IS NULL`); if
    a concurrent request already confirmed the row since this request read it, this call takes
    the same idempotent success path a genuine double-click would (still gated by `expires_at`,
    as above) rather than erroring or double-writing -- unlike `confirm_by_code`, two concurrent
    *correct* token submissions are supposed to both "succeed" from the caller's point of view,
    since a token isn't single-use by design.
    """
    result: EmailVerificationResult | None = None
    error: EmailVerificationError | None = None

    with transaction.atomic():
        try:
            verification = EmailVerification.objects.select_for_update().get(
                token_hash=_digest(token), purpose=purpose
            )
        except EmailVerification.DoesNotExist:
            error = EmailVerificationNotFound()
        else:
            if verification.confirmed_at is not None:
                if timezone.now() < verification.expires_at:
                    result = EmailVerificationResult(
                        email=verification.email, verification_id=verification.pk
                    )
                else:
                    error = EmailVerificationExpired()
            elif verification.invalidated_at is not None:
                error = EmailVerificationSuperseded()
            else:
                now = timezone.now()
                if now >= verification.expires_at:
                    error = EmailVerificationExpired()
                else:
                    EmailVerification.objects.filter(
                        pk=verification.pk, confirmed_at__isnull=True
                    ).update(confirmed_at=now)
                    # Whether this call's own conditional update won the race or a concurrent
                    # one already confirmed the row first, the outcome here is identical: report
                    # success -- that's exactly the idempotent case this function is designed to
                    # tolerate.
                    result = EmailVerificationResult(
                        email=verification.email, verification_id=verification.pk
                    )

    if error is not None:
        raise error
    assert result is not None
    return result


def purge_old(older_than: timedelta = timedelta(days=30)) -> int:
    """Delete verification rows older than `older_than` (by `created_at`). By then they're long
    past their (much shorter) validity window regardless of outcome, so nothing keeps needing
    them beyond occasional support/debugging use, which the admin's read-only list still serves
    for anything more recent. Returns how many were deleted. Used by `apps.core.jobs`."""
    cutoff = timezone.now() - older_than
    deleted, _ = EmailVerification.objects.filter(created_at__lt=cutoff).delete()
    return deleted
