"""Promo code rules (issue #70): the one place a code is looked up, checked and redeemed.

Three entry points, each one transaction with the code's row locked so the per-code cap and the
once-per-user rule hold under concurrent requests:

- `redeem(user, raw_code, source=...)`: the redeem form and the redeem link. Checks everything,
  claims one of the code's uses with a conditional `UPDATE` (the cap can't be overshot), inserts
  the `PromoRedemption` (the partial unique constraint is the once-per-user backstop) and adds the
  credits through `add_credits` (`PROMO`, `source='billing'`), all or nothing.
- `validate_for_signup(raw_code, email)` / `reserve_for_signup(user, raw_code)`: the register
  page's promo field, through `apps.accounts.signup_codes` (accounts can't import billing).
  Validity, dates and the email binding are judged at signup and a use is *reserved* (`pending`)
  then, so the user gets the credits when they confirm, even if the code has lapsed meanwhile
  ("honour it anyway", the decision on #70); only once-per-user is a constraint again.
  `grant_reservations(user)` runs when the email is confirmed (`accounts.signals.email_confirmed`),
  and `release_stale_reservations()` (a daily job) gives back the uses of accounts still
  unconfirmed `RESERVATION_MAX_AGE` after signup.

Refusals are `PromoCodeError` subclasses whose `message` is what the user sees. They are specific
where that is harmless (expired, fully redeemed, already redeemed, new accounts only, confirm your
email) and `Invalid code.` both for an unknown code and for one bound to someone else's email, so a
guesser never learns whose a code is. An inactive code is invalid too; a code not yet started says
so (whoever has it knows about the launch).
"""

import logging
import secrets
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services import notifications

from ..models import (
    CreditTransactionType,
    PromoCode,
    PromoRedemption,
    PromoRedemptionSource,
    PromoRedemptionStatus,
)
from ..models.promo import CODE_MAX_LENGTH, CODE_PATTERN
from .credits import add_credits

logger = logging.getLogger(__name__)

#: How long a signup reservation is kept for an account that never confirms its email.
RESERVATION_MAX_AGE = timedelta(days=30)
#: The alphabet of generated codes: no `0 O 1 I` look-alikes, so a printed code reads back.
GENERATED_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
GENERATED_LENGTH = 10
#: The app label the ledger rows and notifications carry.
SOURCE = 'billing'


class PromoCodeError(Exception):
    """A code can't be redeemed; `message` is what the user is told."""

    message = 'Invalid code.'

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class InvalidPromoCode(PromoCodeError):
    """Unknown, malformed, inactive, or bound to another address: all the same answer."""


class PromoCodeNotStarted(PromoCodeError):
    message = 'This code is not active yet.'


class PromoCodeExpired(PromoCodeError):
    message = 'This code has expired.'


class PromoCodeExhausted(PromoCodeError):
    message = 'This code has been fully redeemed.'


class PromoCodeAlreadyRedeemed(PromoCodeError):
    message = 'You already redeemed this code.'


class PromoCodeNewAccountsOnly(PromoCodeError):
    message = 'This code is only for new accounts.'


class EmailNotConfirmed(PromoCodeError):
    message = 'Confirm your email address first.'


def normalize_code(raw: str) -> str:
    """The stored form of a typed code: stripped and upper-cased. `InvalidPromoCode` when it can't
    be a code at all (so nothing is looked up for `' '` or a 500-character string)."""
    code = (raw or '').strip().upper()
    if not CODE_PATTERN.match(code):
        raise InvalidPromoCode
    return code


def generate_code(prefix: str = '', *, length: int = GENERATED_LENGTH) -> str:
    """A random code, `PREFIX-XXXXXXXXXX` with a prefix or just the random part, that no existing
    code uses. The prefix is normalised like a code (`ValidationError` when it isn't one)."""
    prefix = (prefix or '').strip().upper().rstrip('-')
    if prefix and not CODE_PATTERN.match(prefix.ljust(4, 'A')):
        raise ValidationError('A prefix may only have letters, digits and hyphens.')
    head = f'{prefix}-' if prefix else ''
    if len(head) + length > CODE_MAX_LENGTH:
        raise ValidationError('The prefix is too long.')
    for _ in range(20):
        code = head + ''.join(secrets.choice(GENERATED_ALPHABET) for _ in range(length))
        if not PromoCode.objects.filter(code=code).exists():
            return code
    raise RuntimeError('Could not generate an unused promo code.')  # pragma: no cover


def ledger_description(code: PromoCode) -> str:
    """The credits history line: the code, and the campaign when there is one."""
    text = f'Promo code {code.code}'
    if code.campaign:
        text += f' ({code.campaign})'
    return text[:255]


def find_code(raw: str, *, now: datetime | None = None, lock: bool = False) -> PromoCode:
    """The active code `raw` names, within its dates. Raises `InvalidPromoCode` (unknown, malformed
    or inactive), `PromoCodeNotStarted` or `PromoCodeExpired`. `lock=True` takes the row lock the
    redeeming transactions need (`select_for_update`, a no-op on SQLite)."""
    now = now or timezone.now()
    code_text = normalize_code(raw)
    queryset = PromoCode.objects.filter(code=code_text)
    if lock:
        queryset = queryset.select_for_update()
    code = queryset.first()
    if code is None or not code.active:
        raise InvalidPromoCode
    if code.starts_at is not None and code.starts_at > now:
        raise PromoCodeNotStarted
    if code.ends_at is not None and code.ends_at <= now:
        raise PromoCodeExpired
    return code


def check_eligibility(
    code: PromoCode,
    *,
    email: str,
    user: User | None = None,
    account_created_at: datetime | None = None,
    email_confirmed: bool = True,
    now: datetime | None = None,
) -> None:
    """Whether `email`'s holder may redeem `code` now; raises the specific `PromoCodeError`.

    In this order: the email binding (an outsider gets `Invalid code.`), once per user (when a
    `user` is given), the cap, the new-accounts window (when `account_created_at` is given; the
    register page passes nothing, the account is about to be created), the confirmed email.
    """
    now = now or timezone.now()
    if code.allowed_emails and email.strip().lower() not in code.allowed_emails:
        raise InvalidPromoCode
    if user is not None and _has_claim(code, user):
        raise PromoCodeAlreadyRedeemed
    if code.is_exhausted:
        raise PromoCodeExhausted
    if (
        code.new_accounts_within_days is not None
        and account_created_at is not None
        and account_created_at < now - timedelta(days=code.new_accounts_within_days)
    ):
        raise PromoCodeNewAccountsOnly
    if not email_confirmed:
        raise EmailNotConfirmed


def _has_claim(code: PromoCode, user: User) -> bool:
    return PromoRedemption.objects.filter(
        code=code,
        user=user,
        status__in=[PromoRedemptionStatus.PENDING, PromoRedemptionStatus.GRANTED],
    ).exists()


def _claim_use(code: PromoCode) -> None:
    """Take one of the code's uses, or raise `PromoCodeExhausted`: the cap is in the same `UPDATE`
    as the increment, so two redemptions racing for the last use can't both get it."""
    claimed = (
        PromoCode.objects.filter(pk=code.pk)
        .filter(Q(max_redemptions__isnull=True) | Q(claimed_count__lt=F('max_redemptions')))
        .update(claimed_count=F('claimed_count') + 1)
    )
    if not claimed:
        raise PromoCodeExhausted
    code.claimed_count += 1


def _insert_redemption(code: PromoCode, user: User, **fields) -> PromoRedemption:
    """Create the row inside a savepoint; the unique constraint losing a race is `AlreadyRedeemed`
    (the outer transaction, with the claimed use, rolls back with the exception)."""
    try:
        with transaction.atomic():
            return PromoRedemption.objects.create(
                code=code, user=user, credits=code.credits, **fields
            )
    except IntegrityError:
        raise PromoCodeAlreadyRedeemed from None


def _grant(redemption: PromoRedemption, code: PromoCode, user: User) -> None:
    """Add the credits and mark the redemption granted, in the caller's transaction."""
    tx = add_credits(
        user,
        code.credits,
        tx_type=CreditTransactionType.PROMO,
        description=ledger_description(code),
        source=SOURCE,
    )
    redemption.status = PromoRedemptionStatus.GRANTED
    redemption.granted_at = timezone.now()
    redemption.transaction = tx
    redemption.save(update_fields=['status', 'granted_at', 'transaction'])


def _user_may_redeem(user: User) -> bool:
    return bool(user.is_active and user.status == User.STATUS_ACTIVE)


def redeem(user: User, raw: str, *, source: str = PromoRedemptionSource.FORM) -> PromoRedemption:
    """Redeem `raw` for `user` right now: the credits land in this call. Raises a `PromoCodeError`
    and changes nothing when it can't."""
    if not _user_may_redeem(user):
        raise InvalidPromoCode
    with transaction.atomic():
        code = find_code(raw, lock=True)
        check_eligibility(
            code,
            email=user.email,
            user=user,
            account_created_at=user.created_at,
            email_confirmed=user.email_confirmed,
        )
        _claim_use(code)
        redemption = _insert_redemption(code, user, source=source)
        _grant(redemption, code, user)
    logger.info('Promo code %s redeemed by user %s (%d credits)', code.code, user.pk, code.credits)
    return redemption


def validate_for_signup(raw: str, email: str) -> PromoCode:
    """The register page's check of a typed code for the address being registered. No account
    exists yet, so the new-accounts window and the confirmed email are not judged. Raises the
    `PromoCodeError` to show."""
    code = find_code(raw)
    check_eligibility(code, email=email)
    return code


def reserve_for_signup(user: User, raw: str) -> PromoRedemption:
    """Hold one of the code's uses for the account just created with it. The credits come with
    `grant_reservations` once the email is confirmed. Raises a `PromoCodeError` (a cap reached
    between the form check and here, say) and changes nothing then."""
    with transaction.atomic():
        code = find_code(raw, lock=True)
        check_eligibility(code, email=user.email, user=user)
        _claim_use(code)
        return _insert_redemption(code, user, source=PromoRedemptionSource.SIGNUP)


def grant_reservations(user: User) -> list[PromoRedemption]:
    """Turn `user`'s pending reservations into credits: called when their email is confirmed. The
    code's validity is *not* re-checked (judged at signup, see the module docstring). Each is its
    own transaction, and each also leaves an in-app notification, since the user isn't on a page
    that could show a message. Idempotent: a granted reservation is skipped."""
    user = User.objects.get(pk=user.pk)
    if not user.email_confirmed or not _user_may_redeem(user):
        return []
    granted: list[PromoRedemption] = []
    pending = PromoRedemption.objects.filter(
        user=user, status=PromoRedemptionStatus.PENDING
    ).order_by('created_at')
    for redemption_id in list(pending.values_list('pk', flat=True)):
        try:
            with transaction.atomic():
                redemption = (
                    PromoRedemption.objects.select_for_update()
                    .select_related('code')
                    .filter(pk=redemption_id, status=PromoRedemptionStatus.PENDING)
                    .first()
                )
                if redemption is None:
                    continue
                code = redemption.code
                _grant(redemption, code, user)
                _notify_granted(user, code)
            granted.append(redemption)
        except Exception:
            logger.exception('Could not grant promo reservation %s', redemption_id)
    return granted


def _notify_granted(user: User, code: PromoCode) -> None:
    try:
        with transaction.atomic():
            notifications.notify(
                user,
                source=SOURCE,
                title=f'Your promo code added {code.credits} credits',
                body=f'Promo code {code.code} was applied to your account.',
                url=reverse('billing:credits-history'),
                key=f'promo_granted:{code.pk}',
            )
    except Exception:
        logger.exception('Could not notify user %s of promo code %s', user.pk, code.code)


def release_stale_reservations(
    *, now: datetime | None = None, max_age: timedelta = RESERVATION_MAX_AGE, batch: int = 500
) -> int:
    """Give back the uses held by reservations whose account is still unconfirmed `max_age` after
    signup (the daily `billing.release_promo_reservations` job). The reservation is marked
    `released` (the user may redeem the code on the form later, if it is still valid) and the
    code's `claimed_count` goes down by one. Returns how many were released. Idempotent."""
    now = now or timezone.now()
    cutoff = now - max_age
    released = 0
    while True:
        ids = list(
            PromoRedemption.objects.filter(
                status=PromoRedemptionStatus.PENDING,
                created_at__lt=cutoff,
                user__email_confirmed=False,
            ).values_list('pk', flat=True)[:batch]
        )
        if not ids:
            return released
        for redemption_id in ids:
            try:
                with transaction.atomic():
                    updated = PromoRedemption.objects.filter(
                        pk=redemption_id, status=PromoRedemptionStatus.PENDING
                    ).update(status=PromoRedemptionStatus.RELEASED, released_at=now)
                    if not updated:
                        continue
                    code_id = PromoRedemption.objects.filter(pk=redemption_id).values_list(
                        'code_id', flat=True
                    )[0]
                    PromoCode.objects.filter(pk=code_id, claimed_count__gt=0).update(
                        claimed_count=F('claimed_count') - 1
                    )
                released += 1
            except Exception:
                logger.exception('Could not release promo reservation %s', redemption_id)
        if len(ids) < batch:
            return released


class PromoSignupCodeHandler:
    """`apps.accounts.signup_codes.SignupCodeHandler` for promo codes, registered by
    `BillingConfig.ready()`: the register page validates a typed code through `validate` and,
    once the account exists, reserves it through `attach`."""

    @staticmethod
    def validate(code: str, email: str) -> None:
        try:
            validate_for_signup(code, email)
        except PromoCodeError as exc:
            raise ValidationError(exc.message) from None

    @staticmethod
    def attach(user: User, code: str) -> None:
        try:
            reserve_for_signup(user, code)
        except PromoCodeError as exc:
            # The form check passed moments ago; the cap was reached in between. The account is
            # created either way, and the user may redeem the code on the form if it still fits.
            logger.info('Promo code not reserved at signup for user %s: %s', user.pk, exc.message)
