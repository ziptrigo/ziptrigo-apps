"""Shared login-throttling logic (issue #53 code review) for the two login surfaces -- the
session view (`apps.accounts.views.login.login_page`) and the JWT endpoint
(`apps.accounts.routers.auth.login`) -- which must apply the exact same rules against the exact
same counters, so a rate limit dodge through one surface can't be used to attack the other (see
CLAUDE.md's Auth section for why there are two surfaces at all).

The problem with a single, plain per-account limit (this project's original design, and a common
one elsewhere): keying *only* on the submitted email and counting *every* attempt means anyone who
merely knows a victim's email address can lock them out just by submitting it repeatedly with a
wrong password from anywhere -- a denial-of-service against the real account owner, not the
attacker. Two rules fix that without giving up throttling scripted brute force:

- `LOGIN_ACCOUNT_IP` (`settings.RATELIMIT_RULES`): strict, keyed on (client IP, submitted email).
  Checked *and* incremented before `authenticate()` runs, on every attempt regardless of outcome
  (`ratelimit.hit_ip_and_value`) -- throttles one attacking IP hammering one target account hard.
  Because it's scoped to that one IP, it can never affect the real owner's own login from their
  own IP/device, no matter how many attempts an attacker burns elsewhere.
- `LOGIN_ACCOUNT`: looser, keyed on the submitted email alone, but counts only *failed* attempts --
  checked with `ratelimit.peek_value` (read-only) before `authenticate()`, and only recorded with
  `ratelimit.hit_value` once `authenticate()` has actually returned `None`. A backstop against the
  same account being brute-forced from many different IPs (each with its own `LOGIN_ACCOUNT_IP`
  budget) -- and, because a *successful* login never adds to it, a third party sending wrong
  passwords can never use this rule to lock the real owner out of logging in with the right one.

`LOGIN_IP` (client IP alone, no target email) is checked separately by each caller, same as
before -- this module only covers the two account-scoped rules above.
"""

from __future__ import annotations

from django.http import HttpRequest

from apps.core import ratelimit
from apps.core.ratelimit import RateLimitResult


def check_before_authenticate(request: HttpRequest, email: str) -> RateLimitResult:
    """Call before `authenticate()` runs for `email`. Checks (and, if still within budget,
    increments) the strict `LOGIN_ACCOUNT_IP` rule first; only if that's still allowed does it
    peek (never increment) at the looser `LOGIN_ACCOUNT` ceiling. Returns whichever result would
    block the request, or the (allowed) `LOGIN_ACCOUNT` peek otherwise -- callers only need
    `result.allowed`/`.retry_after`, same as any other rate limit result.
    """
    strict = ratelimit.hit_ip_and_value(request, email, 'LOGIN_ACCOUNT_IP')
    if not strict.allowed:
        return strict
    return ratelimit.peek_value(email, 'LOGIN_ACCOUNT')


def record_failed_attempt(email: str) -> None:
    """Call once `authenticate()` has returned `None` for `email` (wrong password or unknown
    account) -- records one failed attempt against the looser `LOGIN_ACCOUNT` ceiling. Never call
    this for a login blocked by something other than `authenticate()` itself returning `None`
    (e.g. an inactive or unconfirmed account) -- that wasn't a wrong password, so it must not
    count toward it (see the module docstring)."""
    ratelimit.hit_value(email, 'LOGIN_ACCOUNT')
