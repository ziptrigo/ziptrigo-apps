"""The trusted client IP for every public, unauthenticated (or rate-limited) view on the site:
`file_transfer`'s `DownloadEvent.ip`/`Transfer.sender_ip` and per-IP-per-day anonymous caps,
and every per-IP rate limit in `apps.core.ratelimit` (issue #53) all read this same value, so
there is exactly one place that decides how to trust a proxy header.

Originally lived in `apps.file_transfer.services.client_ip`; moved to `core` (issue #53) so
`accounts` and `qr_code` can use it too without a cross-product import.
"""

import ipaddress

from django.conf import settings
from django.http import HttpRequest

type _IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def _trusted_proxy_networks() -> list[_IPNetwork]:
    """Parse `settings.TRUSTED_PROXIES` (a list of bare IPs and/or CIDRs) fresh on every call --
    cheap (a handful of entries), and keeps this correct under `override_settings` in tests rather
    than baking a stale list in at import time."""
    networks: list[_IPNetwork] = []
    for entry in settings.TRUSTED_PROXIES:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted_proxy(remote_addr: str) -> bool:
    try:
        addr = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(addr in network for network in _trusted_proxy_networks())


def client_ip(request: HttpRequest) -> str | None:
    """Reads `X-Real-IP`, but **only** when `REMOTE_ADDR` -- the actual TCP peer, which a client
    can't spoof -- is itself a trusted proxy (`settings.TRUSTED_PROXIES`, issue #53 code review):
    our own nginx sets `X-Real-IP` from the real client and reaches gunicorn over a private Docker
    bridge address, which is why the default trust list is loopback + RFC 1918 + `fc00::/7` (see
    `config/settings.py`). Without that gate, anyone who could reach this app directly (a
    misconfigured proxy, a port left open, or simply not being behind nginx at all) could set
    `X-Real-IP` to whatever they like -- rotating it to dodge a per-IP rate limit, or setting it to
    a victim's real IP to drain that victim's own limits instead of the attacker's.

    Falls back to `REMOTE_ADDR` when `REMOTE_ADDR` isn't a trusted proxy (direct/local access:
    dev, tests, or nginx genuinely misconfigured to not set the header), and also falls back to
    `REMOTE_ADDR` when a trusted proxy's own `X-Real-IP` value fails to parse as an IP address --
    never straight to `None`/"no IP" just because the header was malformed, since every per-IP
    rate limit in this project treats `None` as *unlimited* (see `apps.core.ratelimit.keys`), and
    a bad header must never be a bypass. Returns `None` only when there's truly no usable address
    at all (`REMOTE_ADDR` itself missing or unparseable) -- which shouldn't happen for a real
    request; every WSGI/ASGI server sets `REMOTE_ADDR`.
    """
    remote_addr = request.META.get('REMOTE_ADDR')
    header_value = request.META.get('HTTP_X_REAL_IP')

    candidate = None
    if header_value and remote_addr and _is_trusted_proxy(remote_addr):
        candidate = header_value

    if candidate is None:
        candidate = remote_addr

    if not candidate:
        return None

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        if candidate != remote_addr and remote_addr:
            try:
                ipaddress.ip_address(remote_addr)
            except ValueError:
                return None
            return remote_addr
        return None

    return candidate
