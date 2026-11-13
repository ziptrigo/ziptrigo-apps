import logging
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.costs import CostSection, credits_text
from apps.core.products import ProductApp, products_for

from ..models import BillingSettings
from ..services import get_balance

logger = logging.getLogger(__name__)

#: The storage example on the page: this many GB kept this many days.
EXAMPLE_GB = 2
EXAMPLE_DAYS = 30

_CENT = Decimal('0.01')


def storage_example(price_per_gb_per_day: Decimal) -> str:
    """`2 GB kept for 30 days costs 6 credits.`; a total under a hundredth of a credit says so
    rather than rounding to 0 next to a price that isn't."""
    total = price_per_gb_per_day * EXAMPLE_GB * EXAMPLE_DAYS
    if total < _CENT:
        amount = 'less than 0.01 credits'
    else:
        amount = credits_text(total.quantize(_CENT, rounding=ROUND_HALF_UP))
    return f'{EXAMPLE_GB} GB kept for {EXAMPLE_DAYS} days costs {amount}.'


def _section(product: ProductApp, user: object) -> CostSection | None:
    """The product's section, or `None` (shown as "not available right now") when its provider
    fails: one product's bug must not take the whole page down."""
    if product.costs is None:
        return None
    try:
        return product.costs(user)
    except Exception:
        logger.exception('Costs section of %s failed', product.label)
        return None


@login_required
def costs_page(request: AuthenticatedHttpRequest) -> HttpResponse:
    """What every action of every app costs, prices read live from the settings singletons.

    Every product is listed, the switched-off ones too (a hidden app still charges), in the
    user's order (issue #246). The shared storage price is billing's own; each product's part
    comes from the `costs` provider it registered on its `ProductApp` (`apps.core.costs`).
    """
    user = request.user
    config = BillingSettings.load()
    price = config.storage_price_per_gb_per_day
    sections = [
        {'product': product, 'section': _section(product, user)}
        for product, _visible in products_for(user)
    ]
    context = {
        'credits_balance': get_balance(user),
        'storage_price': credits_text(price) if price > 0 else '',
        'storage_example': storage_example(price),
        'welcome_credits': credits_text(config.welcome_credits) if config.welcome_credits else '',
        'sections': sections,
    }
    return render(request, 'billing/costs.html', context)
