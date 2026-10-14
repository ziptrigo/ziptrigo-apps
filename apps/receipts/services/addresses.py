"""Email addresses and the sender allowlist: normalizing, parsing what a user typed, matching.

The allowlist holds exact addresses and `*@domain` wildcards (matching only that exact domain, not
its subdomains). Both the stored entries and the sender being checked go through
`normalize_address`, so `Me+invoices@Gmail.com` and `me@gmail.com` are the same address.
"""

import re
from collections.abc import Iterable
from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

MAX_ALLOWLIST_ENTRIES = 100
MAX_ADDRESS_LENGTH = 254

_DOMAIN = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')


def normalize_address(address: str) -> str:
    """Lowercase, without wrapping angle brackets, and without a `+tag` on the local part (this
    also covers Gmail's auto-forward envelope senders, `...+caf_=user=example.com@gmail.com`).

    `address` must be a bare addr-spec (what `email.utils.getaddresses`/`parseaddr` return), not a
    `Name <addr>` header value: brackets are removed only when they wrap the whole value. Looking for
    `<...>` anywhere would let a quoted local part (`"x<listed@vendor.com>"@attacker.test`) read as
    `listed@vendor.com` here while the authentication code, which takes the domain after the last
    `@`, vets `attacker.test`."""
    address = address.strip()
    if address.startswith('<') and address.endswith('>'):
        address = address[1:-1]
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
            # An entry may be `Name <addr>`. Only a list item (the API, a JSON list) can hold the
            # spaces of a display name: typed text was split on whitespace above, so there each
            # piece is `Name`, `<addr>`... and `Name` is rejected as invalid.
            normalized = normalize_address(parseaddr(entry)[1] or entry)
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


_CODE = re.compile(r'^[a-z0-9]{1,64}$')


def inbox_code_from_address(address: str) -> str | None:
    """The inbox code in `receipts+<code>@<domain>` -- the inverse of
    `services.accounts.inbox_address` -- or `None` if `address` is not one of this deployment's
    inbox addresses (another domain, no `+code`, or a `receipts@` with nothing after the `+`).
    Case-insensitive; the code is returned lowercase."""
    parsed = parseaddr(address.strip())[1].strip().lower()
    local, at, domain = parsed.rpartition('@')
    base_local, _, base_domain = settings.RECEIPTS_INBOUND_ADDRESS.strip().lower().partition('@')
    base_local = base_local.split('+', 1)[0]
    if not at or not base_domain or domain != base_domain:
        return None
    prefix = f'{base_local}+'
    if not local.startswith(prefix):
        return None
    code = local[len(prefix) :]
    return code if _CODE.match(code) else None
