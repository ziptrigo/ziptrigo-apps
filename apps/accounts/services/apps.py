"""Which product apps a user sees, and in what order (issue #246).

Stored on the user as `app_order` and `hidden_apps` (lists of product labels). `core` reads them
back in `apps.core.products.products_for`; this module is the one place they are written.
"""

from collections.abc import Iterable

from apps.core.products import get_products

from ..models import User


def set_app_preferences(user: User, order: Iterable[str], enabled: Iterable[str]) -> None:
    """Save `order` (the user's order of the product labels) and switch off what `enabled` omits.

    `enabled` is the labels switched on. A product the caller's `order` doesn't list (added since
    its page was rendered) is never hidden by being missing from `enabled`: only labels in `order`
    can be switched off, and with an empty `order` every product not in `enabled` is. The caller
    has validated the labels (`apps.accounts.forms.AppsForm`); an unknown one is a bug and raises
    `ValueError` rather than being stored. Repeats count once, and a registered product missing
    from `order` goes last in registry order, so the stored order always lists every product.
    Switched-off apps keep their place, so switching one back on puts it where it was.
    """
    registered = [product.label for product in get_products()]
    known = set(registered)
    ordered = list(dict.fromkeys(order))
    enabled_set = set(enabled)
    unknown = (set(ordered) | enabled_set) - known
    if unknown:
        raise ValueError(f'Unknown apps: {sorted(unknown)!r}')
    hidden_set = known - enabled_set
    if ordered:
        hidden_set &= set(ordered)
    ordered.extend(label for label in registered if label not in ordered)

    user.app_order = ordered
    user.hidden_apps = [label for label in ordered if label in hidden_set]
    user.save(update_fields=['app_order', 'hidden_apps', 'updated_at'])
