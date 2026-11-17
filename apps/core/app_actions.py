"""Per-app actions on the account page's "Apps" section (`{% app_actions %}`).

Each product may register an `actions` callable on its `ProductApp` (`apps.core.products`) that
takes the request of the account page (`request.user` is signed in) and returns an `AppActions`:
a template, the context to render it with and the static scripts it needs, or `None` when there
is nothing to show. `core` lays the blocks out (one per product, in the user's order, a heading
with the app's icon and name); it can't import a product, so the product owns its block's markup.
Like the Costs page's providers, a provider must not create the product's account row just by
being asked.

A block's scripts are collected by the tag, each path once however many blocks ask for it, and
written as classic (not deferred) `<script>` tags before the blocks, so an `alpine:init` listener
in one is registered before the deferred Alpine starts.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest


@dataclass(frozen=True, slots=True)
class AppActions:
    """A product's block on the account page."""

    template: str
    """Template rendered inside the product's block (with the request, so `{% csrf_token %}`,
    `{% url %}` and the like work)."""

    context: dict[str, Any] = field(default_factory=dict)

    scripts: tuple[str, ...] = ()
    """Static paths (`{% static %}` names) of the scripts the block needs, loaded once each."""


ActionsProvider = Callable[[HttpRequest], AppActions | None]
"""Takes the account page's request (its user is signed in); `None` = nothing to show."""
