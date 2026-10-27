"""The destination block list (issue #162): hosts that short links may not point at, managed in
the admin (`BlockedDomain`; read its docstring for the exact/wildcard rules).

Checked in two places, which is what makes a block work on links that already exist:

- **At create and destination edit** (`links.create_link` / `update_link` via `check_not_blocked`):
  a refusal that says only "that address cannot be used".
- **At every public request** (`links.is_available`, which the redirect, the password page, the
  preview and the report form all ask): a link whose destination host is blocked renders the same
  neutral unavailable page as any other dead link.

It never says why a link is refused, or that a block list exists, like
`apps.file_transfer.services.blocklist`.
"""

import ipaddress
from collections.abc import Iterable

from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User

from ..models import BlockedDomain, ShortLink, normalize_blocked_value
from .destinations import destination_host
from .errors import BlockedDomainError


def _candidates(host: str) -> list[str]:
    """Every stored value that would block `host`: the host itself, and the wildcard of each of
    its suffixes of two labels or more (a wildcard also covers its own bare host, so `*.a.b.com`
    is among the candidates of `a.b.com` as well as of `x.a.b.com`). An IP address can only match
    exactly."""
    host = host.strip().lower().rstrip('.').strip('[]')
    if not host:
        return []
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    labels = host.split('.')
    wildcards = [f'*.{".".join(labels[i:])}' for i in range(len(labels) - 1)]
    return [host, *wildcards]


def _active():
    return BlockedDomain.objects.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    )


def is_blocked(host: str) -> bool:
    """Whether `host` (a stored destination's host: lower-case ASCII) is on the block list right
    now. One indexed query; an expired entry doesn't count."""
    candidates = _candidates(host)
    return bool(candidates) and _active().filter(value__in=candidates).exists()


def blocked_hosts(hosts: Iterable[str]) -> set[str]:
    """The subset of `hosts` that is on the block list right now, for a whole page of links at
    once: one query for all of them (not one per host), then matched in Python with the same
    rules as `is_blocked`."""
    wanted = {host: _candidates(host) for host in set(hosts)}
    every = {value for values in wanted.values() for value in values}
    if not every:
        return set()
    blocked = set(_active().filter(value__in=every).values_list('value', flat=True))
    return {host for host, values in wanted.items() if blocked.intersection(values)}


def check_not_blocked(host: str) -> None:
    """`is_blocked`, raising `BlockedDomainError` for a create or an edit."""
    if is_blocked(host):
        raise BlockedDomainError()


def _block(value: str, *, reason: str, created_by: User) -> BlockedDomain | None:
    """Add `value` to the block list, or `None` if it is already actively blocked.

    Looks only among *active* rows, like file transfer's block list: a plain `get_or_create`
    would match an expired row and silently block nothing. The most recent lapsed row for the
    value is reactivated instead of a new one created, keeping the history of who blocked it.
    """
    value = normalize_blocked_value(value)
    if _active().filter(value=value).exists():
        return None
    lapsed = BlockedDomain.objects.filter(value=value).order_by('-created_at').first()
    if lapsed is not None:
        lapsed.expires_at = None
        lapsed.reason = reason
        lapsed.created_by = created_by
        lapsed.save(update_fields=['expires_at', 'reason', 'created_by'])
        return lapsed
    return BlockedDomain.objects.create(value=value, reason=reason, created_by=created_by)


def block_link_destination(
    link: ShortLink, *, created_by: User, reason: str = '', include_subdomains: bool = True
) -> BlockedDomain | None:
    """Admin convenience: block the host of `link`'s destination (as `*.host`, which covers the
    host and its subdomains, or exactly the host). Returns the entry created or reactivated, or
    `None` when it was already blocked (or the destination has no host)."""
    host = destination_host(link.destination)
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        include_subdomains = False  # an IP address can only be blocked exactly
    except ValueError:
        pass
    value = f'*.{host}' if include_subdomains else host
    return _block(value, reason=reason, created_by=created_by)
