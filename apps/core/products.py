"""Registry of the product apps that make up the site.

Each product app (QR codes, file transfer, ...) registers itself from its ``AppConfig.ready()``.
`core` builds the site navigation and the landing page from this registry, so it never has to
import a product app -- the dependency only runs the other way.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProductApp:
    """A product shown in the site navigation and on the landing page."""

    label: str
    """The Django app label. Used as the registry key."""

    name: str
    """Display name."""

    description: str
    """One-line description for the landing page."""

    url_name: str
    """Name of the URL the product's entry point resolves to, suitable for ``reverse()``."""

    icon: str
    """Font Awesome icon classes, e.g. ``'fas fa-qrcode'``."""


_registry: dict[str, ProductApp] = {}


def register(product: ProductApp) -> None:
    """Register a product app. Registering the same label again replaces the previous entry."""
    _registry[product.label] = product


def get_products() -> list[ProductApp]:
    """Return the registered products, in registration (i.e. ``INSTALLED_APPS``) order."""
    return list(_registry.values())


def _labels(value: object) -> list[str]:
    """`value` as a list of strings; anything else (a missing attribute, junk) is empty."""
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str)]


def products_for(user: object) -> list[tuple[ProductApp, bool]]:
    """Every registered product as ``(product, visible)``, in `user`'s order (issue #246).

    The order and the switched-off apps are plain attributes of the signed-in user
    (``app_order`` and ``hidden_apps``, lists of labels), read the way ``core/base.html`` reads
    ``user.theme``: `core` can't import ``accounts``. An anonymous visitor, or any object without
    the attributes, gets every product in registry order. Read leniently: a label that is no
    longer registered is skipped, a duplicate counts once, and a registered product missing from
    ``app_order`` (one added after the user saved) goes last, in registry order, and is visible.
    """
    registry = {product.label: product for product in get_products()}
    authenticated = bool(getattr(user, 'is_authenticated', False))
    order = _labels(getattr(user, 'app_order', [])) if authenticated else []
    hidden = set(_labels(getattr(user, 'hidden_apps', []))) if authenticated else set()

    ordered: list[ProductApp] = []
    for label in order:
        product = registry.pop(label, None)
        if product is not None:
            ordered.append(product)
    ordered.extend(registry.values())
    return [(product, product.label not in hidden) for product in ordered]


def visible_products(user: object) -> list[ProductApp]:
    """The products `user` has not switched off, in their order (the menu and the home page)."""
    return [product for product, visible in products_for(user) if visible]
