"""Email addresses and the sender allowlist: normalizing, parsing what a user typed, matching.

The allowlist holds exact addresses and `*@domain` wildcards (matching only that exact domain, not
its subdomains). Both the stored entries and the sender being checked go through
`normalize_address`, so `Me+invoices@Gmail.com` and `me@gmail.com` are the same address.
"""

import re
from collections.abc import Iterable

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

MAX_ALLOWLIST_ENTRIES = 100
MAX_ADDRESS_LENGTH = 254

_DOMAIN = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
_ANGLE = re.compile(r'<([^<>]+)>')


def normalize_address(address: str) -> str:
    """Lowercase, without a display name or angle brackets, and without a `+tag` on the local part
    (this also covers Gmail's auto-forward envelope senders, `...+caf_=user=example.com@gmail.com`)."""
    address = address.strip()
    match = _ANGLE.search(address)
    if match:
        address = match.group(1)
    address = address.strip().lower()
    local, at, domain = address.rpartition('@')
    if not at:
        return address
    return f'{local.split("+", 1)[0]}@{domain}'


def parse_allowlist(value: str | Iterable[str] | None) -> list[str]:
    """Turn what the user typed (entries separated by commas, semicolons, spaces or new lines) or
    a list of entries into the normalized, de-duplicated allowlist.

    Raises:
        ValidationError: an entry is not an email address or `*@domain`, or there are too many.
    """
    if value is None:
        return []
    if isinstance(value, str):
        entries = [part for part in re.split(r'[,;\s]+', value) if part]
    else:
        entries = [part.strip() for part in value if part and part.strip()]

    result: list[str] = []
    invalid: list[str] = []
    for entry in entries:
        entry = entry.lower()
        if entry.startswith('*@'):
            domain = entry[2:]
            normalized = entry if _DOMAIN.match(domain) else ''
        else:
            normalized = normalize_address(entry)
            try:
                validate_email(normalized)
            except ValidationError:
                normalized = ''
            if len(normalized) > MAX_ADDRESS_LENGTH:
                normalized = ''
        if not normalized:
            invalid.append(entry)
        elif normalized not in result:
            result.append(normalized)
    if invalid:
        raise ValidationError(
            'Use email addresses or *@domain.com. Not valid: ' + ', '.join(invalid[:5])
        )
    if len(result) > MAX_ALLOWLIST_ENTRIES:
        raise ValidationError(f'At most {MAX_ALLOWLIST_ENTRIES} accepted senders.')
    return result


def matches_allowlist(allowlist: Iterable[str], *addresses: str) -> bool:
    """Whether any of `addresses` is allowed. An empty allowlist allows everyone."""
    entries = set(allowlist)
    if not entries:
        return True
    for address in addresses:
        normalized = normalize_address(address)
        if not normalized:
            continue
        if normalized in entries:
            return True
        _, _, domain = normalized.rpartition('@')
        if f'*@{domain}' in entries:
            return True
    return False
