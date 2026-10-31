"""`apps.billing.services.promo` (issue #70): codes, eligibility, redeeming, signup reservations,
granting on confirmation and releasing stale reservations."""

from datetime import timedelta
from typing import cast

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.billing.models import (
    CreditTransactionType,
    PromoCode,
    PromoRedemption,
    PromoRedemptionSource,
    PromoRedemptionStatus,
)
from apps.billing.services import get_balance, promo
from apps.core.models import Notification

pytestmark = [pytest.mark.django_db, pytest.mark.unit]


def make_code(code='LAUNCH2026', credits=500, **fields) -> PromoCode:
    return PromoCode.objects.create(code=code, credits=credits, **fields)


def make_user(**fields) -> User:
    return cast(User, UserFactory(**fields))


def confirmed_user(**fields) -> User:
    user = make_user(**fields)
    user.email_confirmed = True
    user.email_confirmed_at = timezone.now()
    user.save(update_fields=['email_confirmed', 'email_confirmed_at'])
    return user


# -- codes --


@pytest.mark.parametrize('raw', ['launch2026', ' Launch2026 ', 'LAUNCH2026'])
def test_normalize_code_is_case_insensitive_and_strips(raw):
    assert promo.normalize_code(raw) == 'LAUNCH2026'


@pytest.mark.parametrize('raw', ['', '   ', 'abc', 'a' * 33, 'has space', 'ümlaut1', 'x;y1'])
def test_normalize_code_refuses_what_cannot_be_a_code(raw):
    with pytest.raises(promo.InvalidPromoCode):
        promo.normalize_code(raw)


def test_generate_code_shape_and_prefix():
    plain = promo.generate_code()
    assert len(plain) == promo.GENERATED_LENGTH
    assert set(plain) <= set(promo.GENERATED_ALPHABET)
    with_prefix = promo.generate_code('event-')
    assert with_prefix.startswith('EVENT-')
    assert len(with_prefix) == len('EVENT-') + promo.GENERATED_LENGTH
    promo.normalize_code(with_prefix)  # a valid code


def test_generate_code_refuses_a_bad_or_long_prefix():
    with pytest.raises(ValidationError):
        promo.generate_code('bad prefix')
    with pytest.raises(ValidationError):
        promo.generate_code('P' * 30)


def test_generate_code_avoids_an_existing_code(monkeypatch):
    make_code('AAAAAAAAAA')
    choices = iter(list('AAAAAAAAAA') + list('BBBBBBBBBB'))
    monkeypatch.setattr(promo.secrets, 'choice', lambda alphabet: next(choices))
    assert promo.generate_code() == 'BBBBBBBBBB'


# -- redeem --


def test_redeem_adds_credits_and_records_everything():
    user = confirmed_user()
    code = make_code(campaign='Launch')

    redemption = promo.redeem(user, 'launch2026')

    assert get_balance(user) == 500
    assert redemption.status == PromoRedemptionStatus.GRANTED
    assert redemption.source == PromoRedemptionSource.FORM
    assert redemption.credits == 500
    assert redemption.granted_at is not None
    tx = redemption.transaction
    assert tx is not None
    assert tx.type == CreditTransactionType.PROMO
    assert tx.source == 'billing'
    assert tx.description == 'Promo code LAUNCH2026 (Launch)'
    code.refresh_from_db()
    assert code.claimed_count == 1


def test_ledger_description_without_campaign():
    assert promo.ledger_description(make_code()) == 'Promo code LAUNCH2026'


def test_unknown_code_is_invalid():
    with pytest.raises(promo.InvalidPromoCode) as exc:
        promo.redeem(confirmed_user(), 'NOPE1234')
    assert exc.value.message == 'Invalid code.'


def test_inactive_code_is_invalid():
    make_code(active=False)
    with pytest.raises(promo.InvalidPromoCode):
        promo.redeem(confirmed_user(), 'LAUNCH2026')


def test_not_started_and_expired_codes():
    now = timezone.now()
    make_code('SOON1', starts_at=now + timedelta(hours=1))
    make_code('GONE1', ends_at=now - timedelta(seconds=1))
    user = confirmed_user()
    with pytest.raises(promo.PromoCodeNotStarted):
        promo.redeem(user, 'SOON1')
    with pytest.raises(promo.PromoCodeExpired):
        promo.redeem(user, 'GONE1')
    assert get_balance(user) == 0


def test_once_per_user():
    user = confirmed_user()
    make_code()
    promo.redeem(user, 'LAUNCH2026')
    with pytest.raises(promo.PromoCodeAlreadyRedeemed):
        promo.redeem(user, 'LAUNCH2026')
    assert get_balance(user) == 500
    assert PromoRedemption.objects.count() == 1


def test_a_user_may_redeem_several_different_codes():
    user = confirmed_user()
    make_code('ONE11', credits=1)
    make_code('TWO22', credits=2)
    promo.redeem(user, 'ONE11')
    promo.redeem(user, 'TWO22')
    assert get_balance(user) == 3


def test_max_redemptions_cap():
    code = make_code(max_redemptions=2)
    promo.redeem(confirmed_user(), 'LAUNCH2026')
    promo.redeem(confirmed_user(), 'LAUNCH2026')
    third = confirmed_user()
    with pytest.raises(promo.PromoCodeExhausted):
        promo.redeem(third, 'LAUNCH2026')
    assert get_balance(third) == 0
    code.refresh_from_db()
    assert code.claimed_count == 2
    assert code.is_exhausted


def test_claim_use_is_a_conditional_update():
    """The cap is checked in the same `UPDATE` that increments, so a stale instance can't
    overshoot it."""
    code = make_code(max_redemptions=1)
    PromoCode.objects.filter(pk=code.pk).update(claimed_count=1)
    with pytest.raises(promo.PromoCodeExhausted):
        promo._claim_use(code)
    code.refresh_from_db()
    assert code.claimed_count == 1


def test_email_bound_code_answers_invalid_to_others():
    make_code(allowed_emails=['vip@example.com'])
    outsider = confirmed_user(email='someone@example.com')
    with pytest.raises(promo.InvalidPromoCode):
        promo.redeem(outsider, 'LAUNCH2026')
    vip = confirmed_user(email='VIP@example.com')
    promo.redeem(vip, 'LAUNCH2026')
    assert get_balance(vip) == 500


def test_new_accounts_only():
    make_code(new_accounts_within_days=7)
    old = confirmed_user()
    User.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=8))
    old.refresh_from_db()
    with pytest.raises(promo.PromoCodeNewAccountsOnly):
        promo.redeem(old, 'LAUNCH2026')
    fresh = confirmed_user()
    promo.redeem(fresh, 'LAUNCH2026')


def test_unconfirmed_email_is_refused():
    make_code()
    user = make_user()
    with pytest.raises(promo.EmailNotConfirmed):
        promo.redeem(user, 'LAUNCH2026')
    assert get_balance(user) == 0


def test_inactive_user_is_refused():
    make_code()
    user = confirmed_user(status=User.STATUS_INACTIVE)
    with pytest.raises(promo.InvalidPromoCode):
        promo.redeem(user, 'LAUNCH2026')


def test_refusal_order_specific_where_harmless():
    """An outsider on an email-bound code hears "Invalid code" even when the code has also run
    out; the owner hears the real reason."""
    make_code(allowed_emails=['vip@example.com'], max_redemptions=1)
    PromoCode.objects.update(claimed_count=1)
    with pytest.raises(promo.InvalidPromoCode):
        promo.redeem(confirmed_user(email='x@example.com'), 'LAUNCH2026')
    with pytest.raises(promo.PromoCodeExhausted):
        promo.redeem(confirmed_user(email='vip@example.com'), 'LAUNCH2026')


# -- signup --


def test_validate_for_signup_skips_account_checks_but_keeps_the_binding():
    make_code(new_accounts_within_days=1, allowed_emails=['vip@example.com'])
    assert promo.validate_for_signup('launch2026', 'VIP@example.com').code == 'LAUNCH2026'
    with pytest.raises(promo.InvalidPromoCode):
        promo.validate_for_signup('launch2026', 'other@example.com')


def test_reserve_then_grant_on_confirmation(django_capture_on_commit_callbacks):
    code = make_code(max_redemptions=1, campaign='Welcome')
    user = make_user()  # unconfirmed, as right after signup

    reservation = promo.reserve_for_signup(user, 'launch2026')

    assert reservation.status == PromoRedemptionStatus.PENDING
    assert reservation.source == PromoRedemptionSource.SIGNUP
    code.refresh_from_db()
    assert code.claimed_count == 1  # the use is held
    assert get_balance(user) == 0
    # Nobody else can take the last use meanwhile.
    with pytest.raises(promo.PromoCodeExhausted):
        promo.redeem(confirmed_user(), 'launch2026')

    # Not confirmed yet: nothing granted.
    assert promo.grant_reservations(user) == []
    assert get_balance(user) == 0

    user.email_confirmed = True
    user.save(update_fields=['email_confirmed'])
    granted = promo.grant_reservations(user)

    assert [r.pk for r in granted] == [reservation.pk]
    assert get_balance(user) == 500
    reservation.refresh_from_db()
    assert reservation.status == PromoRedemptionStatus.GRANTED
    assert reservation.transaction is not None
    notice = Notification.objects.get(user_id=user.pk)
    assert notice.source == 'billing'
    assert notice.title == 'Your promo code added 500 credits'
    assert 'LAUNCH2026' in notice.body
    assert notice.url == '/billing/credits/'
    # Idempotent.
    assert promo.grant_reservations(user) == []
    assert get_balance(user) == 500


def test_reservation_is_honoured_after_the_code_lapsed():
    """Judged at signup: a code that expired or ran out since still pays on confirmation."""
    code = make_code(max_redemptions=1)
    user = make_user()
    promo.reserve_for_signup(user, 'LAUNCH2026')
    code.ends_at = timezone.now() - timedelta(days=1)
    code.active = False
    code.save()
    user.email_confirmed = True
    user.save(update_fields=['email_confirmed'])

    promo.grant_reservations(user)

    assert get_balance(user) == 500


def test_reserve_twice_is_refused():
    make_code()
    user = make_user()
    promo.reserve_for_signup(user, 'LAUNCH2026')
    with pytest.raises(promo.PromoCodeAlreadyRedeemed):
        promo.reserve_for_signup(user, 'LAUNCH2026')


def test_release_stale_reservations():
    code = make_code(max_redemptions=5)
    stale = make_user()
    fresh = make_user()
    confirmed_late = make_user()
    for user in (stale, fresh, confirmed_late):
        promo.reserve_for_signup(user, 'LAUNCH2026')
    old = timezone.now() - timedelta(days=31)
    PromoRedemption.objects.filter(user__in=[stale, confirmed_late]).update(created_at=old)
    confirmed_late.email_confirmed = True
    confirmed_late.save(update_fields=['email_confirmed'])

    assert promo.release_stale_reservations() == 1

    code.refresh_from_db()
    assert code.claimed_count == 2
    released = PromoRedemption.objects.get(user=stale)
    assert released.status == PromoRedemptionStatus.RELEASED
    assert released.released_at is not None
    assert PromoRedemption.objects.get(user=fresh).status == PromoRedemptionStatus.PENDING
    assert PromoRedemption.objects.get(user=confirmed_late).status == PromoRedemptionStatus.PENDING
    # Idempotent, and the released user may redeem the code on the form once confirmed.
    assert promo.release_stale_reservations() == 0
    stale.email_confirmed = True
    stale.save(update_fields=['email_confirmed'])
    promo.redeem(stale, 'LAUNCH2026')
    assert get_balance(stale) == 500


# -- the signup hook --


def test_signup_handler_validate_and_attach(caplog):
    handler = promo.PromoSignupCodeHandler()
    make_code(max_redemptions=1)
    with pytest.raises(ValidationError) as exc:
        handler.validate('NOPE1234', 'a@example.com')
    assert exc.value.messages == ['Invalid code.']
    handler.validate('launch2026', 'a@example.com')

    user = make_user()
    handler.attach(user, 'launch2026')
    assert PromoRedemption.objects.filter(user=user, status='pending').exists()
    # A second account for which the cap is now reached: logged, never raised.
    other = make_user()
    handler.attach(other, 'launch2026')
    assert not PromoRedemption.objects.filter(user=other).exists()
