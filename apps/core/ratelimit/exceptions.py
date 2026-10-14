"""`RateLimitExceeded`: the exception ninja endpoints raise instead of building a response inline
(`apps.core.ratelimit.responses` is for plain Django/HTMX views, which have no equivalent
exception-handler pipeline to raise into). `config/api.py` registers the one handler that turns
this into a 429 with a `Retry-After` header, shared by every router.
"""

from __future__ import annotations

import math

from .limiter import RateLimitResult

DEFAULT_MESSAGE = 'Too many requests. Please try again later.'


def retry_message(retry_after: int) -> str:
    """`DEFAULT_MESSAGE` with the wait spelled out ("try again in 42 minutes"), for a rule whose
    window is long enough (an hour) that a bare "wait a moment" would mislead."""
    minutes = max(1, math.ceil(retry_after / 60))
    unit = 'minute' if minutes == 1 else 'minutes'
    return f'Too many requests. Please try again in {minutes} {unit}.'


class RateLimitExceeded(Exception):
    """Raised by `enforce()` when a `RateLimitResult` says the caller is over the limit."""

    def __init__(self, retry_after: int, message: str = DEFAULT_MESSAGE):
        self.retry_after = retry_after
        self.message = message
        super().__init__(message)


def enforce(result: RateLimitResult, message: str = DEFAULT_MESSAGE) -> None:
    """Raise `RateLimitExceeded` if `result` is over the limit; otherwise do nothing. A one-liner
    for ninja router functions: `ratelimit.enforce(ratelimit.hit_ip(request, 'LOGIN_IP'))`.
    """
    if not result.allowed:
        raise RateLimitExceeded(retry_after=result.retry_after, message=message)
