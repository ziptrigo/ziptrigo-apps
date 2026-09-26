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
