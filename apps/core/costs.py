"""What a product charges, for the Costs page (`billing:costs`).

Each product registers a `costs` callable on its `ProductApp` (`apps.core.products`) that takes the
signed-in user and returns a `CostSection`: the actions it charges for, read live from its own
settings singleton, and a line about the user's own usage. `billing` renders the sections; it can't
import a product, and `core` knows no prices, so the products build their lines with the helpers
here and `billing` only lays them out.

Everything here is plain text: the template escapes it.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.template.defaultfilters import filesizeformat

_BYTES_PER_GB = Decimal(1024**3)


@dataclass(frozen=True, slots=True)
class CostLine:
    """One action and what it costs."""

    action: str
    """What the user does, e.g. "Read a receipt with AI"."""

    cost: str
    """The amount as shown: "Free", "1 credit", "10 credits", "Storage"."""

    unit: str = ''
    """What the amount is per, e.g. "per file", "once per link", "every 365 days"."""

    note: str = ''
    """One or two short sentences under the action."""

    free: bool = False
    """The action costs nothing: shown as a grey "Free"."""

    storage: bool = False
    """The action is charged at the shared storage price: the page links to it."""


@dataclass(frozen=True, slots=True)
class CostSection:
    """A product's part of the Costs page."""

    lines: list[CostLine]
    usage: list[str] = field(default_factory=list)
    """Lines about the user's own allowance and charges ("This month: 4 of 10 free tasks used.")."""

    footnote: str = ''
    """What happens when the credits run out, or another closing remark."""


CostProvider = Callable[[Any], CostSection]
"""Takes the signed-in user (`core` can't name `accounts.User`)."""


def credits_text(amount: int | Decimal) -> str:
    """`1 credit`, `10 credits`, `0.1 credits`, `1,000 credits`."""
    if isinstance(amount, Decimal):
        shown = format(amount.normalize(), 'f')
        if '.' not in shown:
            shown = f'{int(shown):,}'
    else:
        shown = f'{amount:,}'
    return f'{shown} credit' if amount == 1 else f'{shown} credits'


def free(action: str, note: str = '') -> CostLine:
    """An action that costs nothing."""
    return CostLine(action=action, cost='Free', note=note, free=True)


def charge(action: str, credits: int | Decimal, unit: str = '', note: str = '') -> CostLine:
    """An action that costs `credits` (an admin-editable price: 0 shows as Free)."""
    if credits <= 0:
        return free(action, note)
    return CostLine(action=action, cost=credits_text(credits), unit=unit, note=note)


def storage(action: str, note: str = '', unit: str = '') -> CostLine:
    """An action charged at the shared storage price (`BillingSettings`)."""
    return CostLine(action=action, cost='Storage', unit=unit, note=note, storage=True)


def plural(count: int, singular: str, plural_form: str = '') -> str:
    """`1 note`, `3 notes` (with `,` thousands)."""
    word = singular if count == 1 else (plural_form or f'{singular}s')
    return f'{count:,} {word}'


def daily_rate_text(rate: Decimal) -> str:
    """`rate` (credits a day): "about 2.5 credits a day", "less than 0.01 credits a day"."""
    if rate <= 0:
        return '0 credits a day'
    if rate < Decimal('0.01'):
        return 'less than 0.01 credits a day'
    shown = rate.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return f'about {credits_text(shown)} a day'


def storage_usage(stored_bytes: int, price_per_gb_per_day: Decimal) -> str:
    """`You store 12.3 MB: less than 0.01 credits a day.` at the shared price, fractions kept
    (what accrues each day; only whole credits are spent)."""
    rate = price_per_gb_per_day * Decimal(stored_bytes) / _BYTES_PER_GB
    return f'You store {filesizeformat(stored_bytes)}: {daily_rate_text(rate)}.'
