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


#: Second-level suffixes under which people register names (`example.co.uk`), so that the
#: "organizational domain" of `mail.example.co.uk` is `example.co.uk`, not `co.uk`. A small
#: hand-kept list of the common ones, NOT the Public Suffix List: a suffix missing here makes two
#: unrelated names under it look aligned (`a.xx.yy` / `b.xx.yy`), which is why the list errs on
#: the side of having too many.
MULTI_LABEL_SUFFIXES = frozenset(
    {
        'co.uk', 'org.uk', 'ac.uk', 'gov.uk', 'me.uk', 'ltd.uk', 'plc.uk', 'net.uk', 'sch.uk',
        'com.au', 'net.au', 'org.au', 'edu.au', 'gov.au', 'asn.au', 'id.au',
        'co.nz', 'org.nz', 'net.nz', 'govt.nz', 'ac.nz',
        'co.jp', 'ne.jp', 'or.jp', 'ac.jp', 'go.jp',
        'co.in', 'net.in', 'org.in', 'ac.in', 'gov.in',
        'co.za', 'org.za', 'net.za', 'gov.za',
        'com.br', 'net.br', 'org.br', 'gov.br',
        'com.mx', 'org.mx', 'com.ar', 'com.co', 'com.pe', 'com.ve', 'com.uy', 'com.ec',
        'com.tr', 'org.tr', 'com.sg', 'com.my', 'com.hk', 'com.tw', 'com.cn', 'net.cn', 'org.cn',
        'co.kr', 'or.kr', 'co.il', 'org.il', 'co.id', 'co.th', 'com.ph', 'com.vn', 'com.pk',
        'com.ua', 'com.pl', 'com.eg', 'com.sa', 'com.ng', 'co.ke', 'com.gr', 'com.pt', 'com.ru',
    }
)  # fmt: skip


def organizational_domain(domain: str) -> str:
    """An approximation of a domain's organizational (registrable) domain: its last two labels,
    or three when those two are a common second-level suffix (`MULTI_LABEL_SUFFIXES`). So
    `mail.example.com` and `example.com` share `example.com`."""
    labels = domain.lower().strip().strip('.').split('.')
    if len(labels) <= 2:
        return '.'.join(labels)
    if '.'.join(labels[-2:]) in MULTI_LABEL_SUFFIXES:
        return '.'.join(labels[-3:])
    return '.'.join(labels[-2:])


def domains_aligned(first: str, second: str) -> bool:
    """DMARC-style *relaxed* alignment, approximated: the two domains are equal, or one is a
    subdomain of the other, or they share an organizational domain (`organizational_domain`).
    Empty never aligns."""
    first, second = first.lower().strip('.'), second.lower().strip('.')
    if not first or not second:
        return False
    return organizational_domain(first) == organizational_domain(second)


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
