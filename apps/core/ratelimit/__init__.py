"""Site-wide rate limiting (issue #53): a small, in-house fixed-window limiter, rather than a
third-party library -- see `limiter.py`'s docstring for the counting scheme and its two storage
backends, and CLAUDE.md for why this over `django-ratelimit` (async ninja endpoints and
django-ninja's own request-parsing pipeline don't compose cleanly with a decorator-based library;
a plain function call at the top of a view is simpler and works identically for sync views, async
views, and ninja routers).

Typical use:

    # A plain Django/HTMX view (session-authenticated or public):
    from apps.core import ratelimit

    result = ratelimit.hit_ip(request, 'QR_REDIRECT_IP')
    if not result.allowed:
        return ratelimit.web_response(request, result)

    # A ninja router endpoint (sync or async):
    ratelimit.enforce(ratelimit.hit_ip(request, 'LOGIN_IP'))
    ratelimit.enforce(await ratelimit.ahit_value(payload.email.lower(), 'QR_CREATE_USER'))

    # A "check before a sensitive operation, only record on failure" limit (`peek`/`peek_value`
    # instead of `hit`/`hit_value` -- see `apps.accounts.services.login_throttle` for the real
    # caller this pattern is for):
    result = ratelimit.peek_value(email, 'LOGIN_ACCOUNT')
    if result.allowed and authenticate(request, ...) is None:
        ratelimit.hit_value(email, 'LOGIN_ACCOUNT')  # record the failure, after the fact

Every rule referenced above (`'QR_REDIRECT_IP'`, `'LOGIN_IP'`, ...) is a name in
`settings.RATELIMIT_RULES`; see that dict's comments in `config/settings.py` for the full table
and the reasoning behind each limit. `settings.RATELIMIT_ENABLE` is the project-wide kill switch.
"""

from .exceptions import RateLimitExceeded, enforce
from .keys import ahit_ip, ahit_value, hit_ip, hit_ip_and_value, hit_user, hit_value, peek_value
from .limiter import CACHE_ALIAS, RateLimitResult, hit, peek, purge_expired
from .responses import htmx_response, json_response, page_response, web_response

__all__ = [
    'CACHE_ALIAS',
    'RateLimitExceeded',
    'RateLimitResult',
    'ahit_ip',
    'ahit_value',
    'enforce',
    'hit',
    'hit_ip',
    'hit_ip_and_value',
    'hit_user',
    'hit_value',
    'htmx_response',
    'json_response',
    'page_response',
    'peek',
    'peek_value',
    'purge_expired',
    'web_response',
]
