import pytest

from apps.billing.models import CreditTransaction
from apps.billing.services import InsufficientCreditsError, add_credits

from ..models import ShortLink
from ..services import Extra, charge_extra, quote
from ..services.billing import is_paid, price
from .conftest import balance
from .factories import create_link

pytestmark = pytest.mark.django_db


def test_prices_come_from_the_settings(prices):
    prices.custom_alias_price = 3
    prices.password_price = 4
    prices.analytics_price = 5
    prices.extra_link_price = 6
    prices.save()
    assert [price(extra) for extra in Extra] == [3, 4, 5, 6]


def test_charge_spends_once_and_records_it(funded_user):
    link = create_link(funded_user)
    assert charge_extra(link, Extra.ALIAS) == 1
    assert balance(funded_user) == 9
    assert is_paid(link, Extra.ALIAS)
    assert ShortLink.objects.get(pk=link.pk).alias_paid_at is not None

    row = CreditTransaction.objects.filter(user=funded_user, source='short_url').get()
    assert row.amount == -1
    assert row.type == 'spend'
    assert link.code in row.description


def test_charging_again_is_free(funded_user):
    link = create_link(funded_user)
    charge_extra(link, Extra.ALIAS)
    assert charge_extra(link, Extra.ALIAS) == 0
    assert balance(funded_user) == 9
    assert CreditTransaction.objects.filter(source='short_url').count() == 1


def test_each_extra_is_paid_on_its_own(funded_user):
    link = create_link(funded_user)
    for extra in Extra:
        assert charge_extra(link, extra) == 1
    assert balance(funded_user) == 6
    link.refresh_from_db()
    assert all(is_paid(link, extra) for extra in Extra)


def test_a_stale_copy_of_the_link_cannot_pay_twice(funded_user):
    """Two requests holding the same link both think it is unpaid: the claim on the column is a
    compare-and-swap, so only one of them spends."""
    link = create_link(funded_user)
    stale = ShortLink.objects.get(pk=link.pk)
    assert charge_extra(link, Extra.ANALYTICS) == 1
    assert stale.analytics_paid_at is None
    assert charge_extra(stale, Extra.ANALYTICS) == 0
    assert balance(funded_user) == 9


def test_a_price_of_zero_marks_it_paid_without_spending(funded_user, prices):
    prices.custom_alias_price = 0
    prices.save()
    link = create_link(funded_user)
    assert charge_extra(link, Extra.ALIAS) == 0
    assert is_paid(link, Extra.ALIAS)
    assert balance(funded_user) == 10
    assert not CreditTransaction.objects.filter(source='short_url').exists()


def test_not_enough_credits_leaves_it_unpaid(user, prices):
    add_credits(user, 1, description='x', source='test')
    link = create_link(user)
    prices.custom_alias_price = 2
    prices.save()
    with pytest.raises(InsufficientCreditsError):
        charge_extra(link, Extra.ALIAS)
    assert balance(user) == 1
    assert not is_paid(link, Extra.ALIAS)
    assert ShortLink.objects.get(pk=link.pk).alias_paid_at is None
    assert not CreditTransaction.objects.filter(source='short_url').exists()


def test_no_balance_at_all_is_insufficient_too(user, prices):
    link = create_link(user)
    with pytest.raises(InsufficientCreditsError):
        charge_extra(link, Extra.EXTRA_LINK)
    assert not is_paid(link, Extra.EXTRA_LINK)


def test_quote_lists_only_extras_that_cost_something(prices):
    prices.extra_link_price = 0
    prices.custom_alias_price = 2
    prices.save()
    result = quote([Extra.ALIAS, Extra.EXTRA_LINK])
    assert [(c.extra, c.credits) for c in result.charges] == [(Extra.ALIAS, 2)]
    assert result.total == 2
    assert result
    assert 'custom alias 2' in result.summary


def test_an_empty_quote_is_falsy(prices):
    assert not quote([])
    prices.custom_alias_price = 0
    prices.save()
    assert not quote([Extra.ALIAS])
    assert quote([]).total == 0
