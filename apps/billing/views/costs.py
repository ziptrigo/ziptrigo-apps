from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from apps.accounts.http import AuthenticatedHttpRequest
from apps.core.costs import credits_text
from apps.core.products import products_for

from ..models import BillingSettings
from ..services import get_balance

#: The storage example on the page: this many GB kept this many days.
EXAMPLE_GB = 2
EXAMPLE_DAYS = 30


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
    example = price * EXAMPLE_GB * EXAMPLE_DAYS
    sections = [
        {'product': product, 'section': product.costs(user)}
        for product, _visible in products_for(user)
        if product.costs is not None
    ]
    context = {
        'credits_balance': get_balance(user),
        'storage_price': credits_text(price) if price > 0 else '',
        'storage_example': (
            f'{EXAMPLE_GB} GB kept for {EXAMPLE_DAYS} days costs '
            f'{credits_text(example.quantize(Decimal("0.01")).normalize())}.'
        ),
        'welcome_credits': credits_text(config.welcome_credits) if config.welcome_credits else '',
        'sections': sections,
    }
    return render(request, 'billing/costs.html', context)
