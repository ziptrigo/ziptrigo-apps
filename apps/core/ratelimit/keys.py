"""Convenience wrappers over `limiter.hit` for the three key shapes every call site in this
project needs: per client IP, per authenticated user, per arbitrary string (email, transfer slug,
...). Each returns `RateLimitResult.unlimited()` -- rather than raising or skipping the check --
when there's simply no key to count against (e.g. `client_ip` couldn't parse a valid address), so
callers never need a separate "was there a key" branch.
"""

from __future__ import annotations

from typing import Protocol

from asgiref.sync import sync_to_async
from django.http import HttpRequest

from .limiter import RateLimitResult, hit


class _HasPk(Protocol):
    """Structural stand-in for `accounts.models.User` (and anything else with a primary key) --
    `apps.core` can't import `apps.accounts` (see CLAUDE.md's layering), so `hit_user` is typed
    structurally instead of importing the real model just for a type hint."""

    pk: object


def hit_ip(request: HttpRequest, rule: str) -> RateLimitResult:
    """Count against the request's trusted client IP (`apps.core.services.client_ip`)."""
    from apps.core.services.client_ip import client_ip

    ip = client_ip(request)
    if ip is None:
        return RateLimitResult.unlimited()
    return hit(ip, rule)


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


# -- Async variants, for the qr_code API's async ninja endpoints and the async `/go/<code>`
# redirect view: `hit()` itself does a synchronous cache round-trip (which, on the `DatabaseCache`
# backend, touches the DB connection and is disallowed straight from an async context -- see
# `django.utils.asyncio.async_unsafe`), so these thread it through `sync_to_async` rather than
# duplicating the counting logic. --


async def ahit_ip(request: HttpRequest, rule: str) -> RateLimitResult:
    from apps.core.services.client_ip import client_ip

    ip = client_ip(request)
    if ip is None:
        return RateLimitResult.unlimited()
    return await sync_to_async(hit)(ip, rule)


async def ahit_value(value: str | None, rule: str) -> RateLimitResult:
    if not value:
        return RateLimitResult.unlimited()
    return await sync_to_async(hit)(value, rule)
