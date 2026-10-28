"""Email addresses and the sender allowlist, for Receipts: the code lives in
`apps.core.services.mail.addresses` (shared with To-do since issue #186); this module re-exports it
and binds `settings.RECEIPTS_INBOUND_ADDRESS` to `inbox_code_from_address`.
"""

from django.conf import settings

from apps.core.services.mail import addresses as _core
from apps.core.services.mail.addresses import (
    MAX_ADDRESS_LENGTH,
    MAX_ALLOWLIST_ENTRIES,
    matches_allowlist,
    normalize_address,
    parse_allowlist,
)

__all__ = [
    'MAX_ADDRESS_LENGTH',
    'MAX_ALLOWLIST_ENTRIES',
    'inbox_code_from_address',
    'matches_allowlist',
    'normalize_address',
    'parse_allowlist',
]


def inbox_code_from_address(address: str) -> str | None:
    """The inbox code in `receipts+<code>@<domain>`, or `None` (see the core function)."""
    return _core.inbox_code_from_address(address, inbound_address=settings.RECEIPTS_INBOUND_ADDRESS)
