"""The register page's optional "promo code" field (issue #70), as a hook.

`accounts` can't import `billing` (CLAUDE.md's layering: `billing` sits above `accounts`), so the
page knows there *is* a code field and nothing about what a code is. `billing` registers a
`SignupCodeHandler` from its `AppConfig.ready()`, the same shape as `apps.core.products` and
`apps.core.scheduler`; without one, a non-empty code is refused as invalid.

`validate(code, email)` runs on the register POST, before the captcha, and raises a
`django.core.exceptions.ValidationError` whose message the form shows under the field.
`attach(user, code)` runs right after the account row exists (unconfirmed) and must not raise: it
records the code against the account, and the handler's own `email_confirmed` receiver finishes
the job once the address is confirmed.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .models import User

#: The query parameter that prefills the field (`/account/register/?promo=CODE`), which the login
#: page's "Create one" link forwards so a redeem link opened without a session keeps its code.
QUERY_PARAM = 'promo'


class SignupCodeHandler(Protocol):
    def validate(self, code: str, email: str) -> None: ...

    def attach(self, user: 'User', code: str) -> None: ...


_handler: SignupCodeHandler | None = None


def register(handler: SignupCodeHandler) -> None:
    """Install the handler (one; registering again replaces it)."""
    global _handler
    _handler = handler


def get_handler() -> SignupCodeHandler | None:
    return _handler
