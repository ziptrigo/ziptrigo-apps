"""The signed cookie half of the anonymous per-IP-per-day caps (spec section 13): a random,
opaque id that identifies a browser across requests independent of its IP, so the cap can be
enforced against whichever of the two (IP or cookie) reports the higher usage.

Not a session cookie: it deliberately outlives the Django session (a long `max_age`, refreshed on
every response from the anonymous send flow), so clearing cookies is the only way to shed it --
clearing just the session wouldn't otherwise reset the count.
"""

import secrets

from django.http import HttpRequest, HttpResponse

COOKIE_NAME = 'ft_anon_id'
_SALT = 'file_transfer.anon_cookie'
#: Long-lived on purpose -- see the module docstring. A little under two years, comfortably under
#: every major browser's absolute cookie lifetime cap.
_MAX_AGE_SECONDS = 60 * 60 * 24 * 700


def read_cookie_id(request: HttpRequest) -> str | None:
    """The browser's existing anonymous cookie id, or `None` if it has none (or an invalid/
    tampered one -- `get_signed_cookie` returns `default` for a bad signature)."""
    value = request.get_signed_cookie(COOKIE_NAME, default=None, salt=_SALT)
    return value if isinstance(value, str) and value else None


def new_cookie_id() -> str:
    return secrets.token_urlsafe(24)


def set_cookie(response: HttpResponse, cookie_id: str) -> None:
    """Set (or refresh) the signed cookie on `response`. Called on every response from the
    anonymous send flow, whether or not the id is new, both to persist a freshly-generated one
    and to slide the expiry forward for a returning sender."""
    response.set_signed_cookie(
        COOKIE_NAME,
        cookie_id,
        salt=_SALT,
        max_age=_MAX_AGE_SECONDS,
        httponly=True,
        samesite='Lax',
    )
