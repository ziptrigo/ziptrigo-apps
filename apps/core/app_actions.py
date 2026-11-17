"""Per-app actions on the account page's "Apps" section (`{% app_actions %}`).

Each product may register an `actions` callable on its `ProductApp` (`apps.core.products`) that
takes the signed-in user and returns an `AppActions`: a template and the context to render it
with, or `None` when there is nothing to show. `core` lays the blocks out (one per product, in the
user's order, a heading with the app's icon and name); it can't import a product, so the product
owns its block's markup. Like the Costs page's providers, a provider must not create the
product's account row just by being asked.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AppActions:
    """A product's block on the account page."""

    template: str
    """Template rendered inside the product's block (with the request, so `{% csrf_token %}`,
    `{% url %}` and the like work)."""

    context: dict[str, Any] = field(default_factory=dict)


ActionsProvider = Callable[[Any], AppActions | None]
"""Takes the signed-in user (`core` can't name `accounts.User`); `None` = nothing to show."""
