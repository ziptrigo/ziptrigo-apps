"""The shared storage price (issue #98): `BillingSettings`, `get_storage_price_per_gb_per_day`
and the `storage_charge` accrual arithmetic."""

from decimal import Decimal

import pytest

from apps.billing.models import BillingSettings
from apps.billing.services import get_storage_price_per_gb_per_day, storage_charge

pytestmark = [pytest.mark.unit]

_GB = 1024**3


def test_whole_credits_with_no_remainder():
    assert storage_charge(Decimal('0'), _GB, Decimal('1')) == (1, Decimal('0'))
    assert storage_charge(Decimal('0'), 3 * _GB, Decimal('2')) == (6, Decimal('0'))


def test_fraction_is_carried_not_spent():
    whole, remainder = storage_charge(Decimal('0'), _GB, Decimal('0.4'))

    assert whole == 0
    assert remainder == Decimal('0.4')


def test_rounds_down_and_keeps_the_remainder():
    whole, remainder = storage_charge(Decimal('0.8'), _GB, Decimal('0.4'))

    assert whole == 1
    assert remainder == Decimal('0.2')


def test_zero_size_accrues_nothing_but_still_spends_a_carried_whole_credit():
    assert storage_charge(Decimal('0'), 0, Decimal('5')) == (0, Decimal('0'))
    assert storage_charge(Decimal('0.3'), 0, Decimal('5')) == (0, Decimal('0.3'))
    assert storage_charge(Decimal('1.3'), 0, Decimal('5')) == (1, Decimal('0.3'))


def test_zero_price_accrues_nothing():
    assert storage_charge(Decimal('0.5'), 10 * _GB, Decimal('0')) == (0, Decimal('0.5'))


def test_fraction_of_a_gb():
    # Half a GB at 1 credit/GB/day is half a credit.
    assert storage_charge(Decimal('0'), _GB // 2, Decimal('1')) == (0, Decimal('0.5'))
    assert storage_charge(Decimal('0.5'), _GB // 2, Decimal('1')) == (1, Decimal('0'))


def test_remainder_carries_across_days_until_a_credit_is_due():
    accrued = Decimal('0')
    spent = []
    for _ in range(5):
        whole, accrued = storage_charge(accrued, _GB, Decimal('0.4'))
        spent.append(whole)

    # 0.4, 0.8, 1.2 -> 1 (0.2 left), 0.6, 1.0 -> 1 (0 left).
    assert spent == [0, 0, 1, 0, 1]
    assert accrued == Decimal('0')


def test_whole_plus_remainder_is_the_new_total():
    accrued, size, price = Decimal('0.35'), 7 * _GB // 3, Decimal('0.1234')

    whole, remainder = storage_charge(accrued, size, price)

    assert 0 <= remainder < 1
    total = accrued + price * (Decimal(size) / Decimal(_GB))
    # Equal to within the remainder's 6-decimal-place precision (half an ulp at most).
    assert abs(whole + remainder - total) <= Decimal('0.0000005')


def test_remainder_is_quantized_to_six_decimal_places():
    # 1 byte at 1 credit/GB/day is 1 / 2**30 = 9.31e-10: far below 6 places.
    whole, remainder = storage_charge(Decimal('0'), 1, Decimal('1'))
    assert (whole, remainder) == (0, Decimal('0.000000'))

    # A repeating fraction is cut to exactly 6 places, rounding half-even.
    whole, remainder = storage_charge(Decimal('0'), _GB, Decimal('1') / Decimal(3))
    assert whole == 0
    assert remainder == Decimal('0.333333')
    assert remainder.as_tuple().exponent == -6


@pytest.mark.django_db
def test_billing_settings_is_a_singleton():
    first = BillingSettings.load()
    assert first.pk == 1
    assert first.storage_price_per_gb_per_day == Decimal('0.1')

    first.pk = 7  # `save()` pins the pk back to 1, so this updates the one row.
    first.storage_price_per_gb_per_day = Decimal('0.25')
    first.save()

    second = BillingSettings.load()
    assert first.pk == second.pk == 1
    assert second.storage_price_per_gb_per_day == Decimal('0.25')
    assert BillingSettings.objects.count() == 1


@pytest.mark.django_db
def test_get_storage_price_reads_billing_settings():
    assert get_storage_price_per_gb_per_day() == Decimal('0.1')

    settings_row = BillingSettings.load()
    settings_row.storage_price_per_gb_per_day = Decimal('0.75')
    settings_row.save()

    assert get_storage_price_per_gb_per_day() == Decimal('0.75')
