"""The trusted client IP for this app's public, unauthenticated views (download page, and phase
2's anonymous send flow): `DownloadEvent.ip`, `Transfer.sender_ip`, and the per-IP-per-day
anonymous caps (spec section 13) all read this same value, so there is exactly one place that
decides how to trust a proxy header.
"""

import ipaddress

from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str | None:
    """Reads `X-Real-IP`, which **must** be set by our own nginx from the actual TCP peer (never
    passed through from the client) -- unlike `X-Forwarded-For`, which the client fully controls
    and which nginx here does not sanitize, so it isn't trustworthy on its own. Falls back to
    `REMOTE_ADDR` for direct/local access (dev, tests, or nginx misconfigured to not set it).
    Returns `None` for anything that doesn't parse as a valid IP address, rather than letting a
    bogus value reach `GenericIPAddressField` and crash the request with a `DataError` on
    Postgres.
    """
    candidate = request.META.get('HTTP_X_REAL_IP') or request.META.get('REMOTE_ADDR')
    if not candidate:
        return None
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate
