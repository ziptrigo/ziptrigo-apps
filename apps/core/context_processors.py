from django.http import HttpRequest

from .products import ProductApp, visible_products


def products(request: HttpRequest) -> dict[str, list[ProductApp]]:
    """Expose the product apps the visitor sees to every template, for the nav and landing page.

    A signed-in user's own order and switched-off apps apply (issue #246); anyone else gets every
    product in registry order.
    """
    return {'products': visible_products(getattr(request, 'user', None))}
