from django.http import HttpRequest

from .products import ProductApp, get_products


def products(request: HttpRequest) -> dict[str, list[ProductApp]]:
    """Expose the registered product apps to every template, for the nav and landing page."""
    return {'products': get_products()}
