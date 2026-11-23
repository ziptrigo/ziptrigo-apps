"""Template helpers of the billing app: the address of the Credits page and the name of an app."""

from django import template

from ..services import usage
from ..services.links import DEFAULT_TAB
from ..services.links import credits_url as build_credits_url

register = template.Library()


@register.simple_tag
def credits_url(tab: str = DEFAULT_TAB) -> str:
    """The Credits page's address for `tab` (`'usage'` or `'history'`): `{% credits_url 'history' %}`.
    Every "Top up" link uses the history tab, where the promo code form is."""
    return build_credits_url(tab)


@register.filter
def app_name(label: object) -> str:
    """The display name of the app a ledger row's `source` names (`''` for none)."""
    return usage.app_name(label) if isinstance(label, str) else ''
