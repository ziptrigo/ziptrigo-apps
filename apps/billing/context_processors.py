from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from .services import get_balance


def credits(request: HttpRequest) -> dict[str, object]:
    """Expose the logged-in user's credit balance to every template as ``credits_balance``.

    Lazy, so pages that never show the balance don't pay for the query.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    return {'credits_balance': SimpleLazyObject(lambda: get_balance(user))}
