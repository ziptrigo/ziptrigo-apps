"""Convenience wrappers over `limiter.hit`/`limiter.peek` for the key shapes call sites in this
project need: per client IP, per authenticated user, per arbitrary string (email, transfer slug,
...), and combinations of those. Each returns `RateLimitResult.unlimited()` -- rather than raising
or skipping the check -- when there's simply no key to count against (e.g. `client_ip` couldn't
parse a valid address), so callers never need a separate "was there a key" branch.
"""

from __future__ import annotations

import ipaddress
from typing import Protocol

from asgiref.sync import sync_to_async
from django.http import HttpRequest

from .limiter import RateLimitResult, hit, peek


class _HasPk(Protocol):
    """Structural stand-in for `accounts.models.User` (and anything else with a primary key) --
    `apps.core` can't import `apps.accounts` (see CLAUDE.md's layering), so `hit_user` is typed
    structurally instead of importing the real model just for a type hint."""

    pk: object


def _normalize_ip_for_key(ip: str) -> str:
    """Normalise a client IP for use as a *rate-limit key* (issue #53 code review) -- never for
    storage or display elsewhere, where the exact address still matters (e.g.
    `file_transfer.models.DownloadEvent.ip`): an IPv4-mapped IPv6 address (`::ffff:a.b.c.d`) is
    unwrapped to its plain IPv4 form, and a genuine IPv6 address is collapsed to its /64 network --
    the block size most residential/mobile ISPs hand a single customer/device, so a host that
    rotates its address within its own /64 (routine for IPv6 privacy extensions) can't dodge a
    per-IP limit just by doing so. IPv4 addresses, and anything that doesn't parse as an IP at
    all, pass through unchanged (the latter can't happen in practice -- `client_ip` itself only
    ever returns a value that already parsed -- but this stays a plain no-op rather than raising,
    consistent with every other helper in this module).
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        network = ipaddress.ip_network(f'{addr}/64', strict=False)
        return str(network.network_address)
    return str(addr)


def hit_ip(request: HttpRequest, rule: str) -> RateLimitResult:
    """Count against the request's trusted client IP (`apps.core.services.client_ip`), normalised
    for IPv6 (see `_normalize_ip_for_key`)."""
    from apps.core.services.client_ip import client_ip

    ip = client_ip(request)
    if ip is None:
        return RateLimitResult.unlimited()
    return hit(_normalize_ip_for_key(ip), rule)


def hit_user(user: _HasPk, rule: str) -> RateLimitResult:
    """Count against an authenticated user's id. Callers pass the user explicitly (`request.user`
    for a session view, `request.auth` for a JWT-authenticated ninja endpoint) rather than this
    helper guessing which attribute holds it."""
    return hit(str(user.pk), rule)


def hit_value(value: str | None, rule: str) -> RateLimitResult:
    """Count against an arbitrary, caller-normalised string: an email address (lowercased,
    stripped), a transfer slug, a verification id, ... `None`/empty is treated as "nothing to
    count", same as `hit_ip` with an unparseable IP.
    """
    if not value:
        return RateLimitResult.unlimited()
    return hit(value, rule)


def peek_value(value: str | None, rule: str) -> RateLimitResult:
    """Like `hit_value`, but reports the current state without counting this call as a hit -- see
    `limiter.peek`'s docstring for why a caller would want that."""
    if not value:
        return RateLimitResult.unlimited()
    return peek(value, rule)


def hit_ip_and_value(request: HttpRequest, value: str | None, rule: str) -> RateLimitResult:
    """Count against the combination of the request's trusted client IP and an arbitrary value
    (typically a submitted email/account identifier) -- a *strict* limit that's meaningfully
    harder for an attacker to abuse against a victim than a plain per-value limit: exhausting it
    for one victim from many different IPs costs one full budget *per IP*, and exhausting it from
    one IP affects only that (value, IP) pair, never the victim's own real IP/traffic. See
    `apps.accounts.services.login_throttle` for the caller this exists for.
    """
    from apps.core.services.client_ip import client_ip

    ip = client_ip(request)
    if ip is None or not value:
        return RateLimitResult.unlimited()
    return hit(f'{_normalize_ip_for_key(ip)}:{value}', rule)


# -- Async variants, for the qr_code API's async ninja endpoints and the async `/go/<code>`
# redirect view: `hit()` itself does a synchronous storage round-trip (which, on the `'db'`
# storage path, touches the DB connection and is disallowed straight from an async context -- see
# `django.utils.asyncio.async_unsafe`), so these thread it through `sync_to_async` rather than
# duplicating the counting logic. --


async def ahit_ip(request: HttpRequest, rule: str) -> RateLimitResult:
    from apps.core.services.client_ip import client_ip

    ip = client_ip(request)
    if ip is None:
        return RateLimitResult.unlimited()
    return await sync_to_async(hit)(_normalize_ip_for_key(ip), rule)


async def ahit_value(value: str | None, rule: str) -> RateLimitResult:
    if not value:
        return RateLimitResult.unlimited()
    return await sync_to_async(hit)(value, rule)
