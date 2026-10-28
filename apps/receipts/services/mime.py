"""Reading a raw inbound email, for Receipts: the parser and the sender-authentication code live in
`apps.core.services.mail.mime` (shared with To-do since issue #186; read its module docstring for
the header shapes and the limits), and this module binds Receipts' parameters to it:

- `scan_recipients` / `recipients_from_addresses` look for `settings.RECEIPTS_INBOUND_ADDRESS`;
- `analyze` sniffs with `documents.sniff_content_type`, chooses a body with
  `documents.html_to_text` and reads inline image sizes with `documents.image_dimensions`;
- `build_stripped_eml` marks parts with `X-Receipts-Receipt-Id` / `X-Receipts-Skipped` (and
  `X-Receipts-Stripped` when only the headers are kept).

Everything else is re-exported unchanged, so `from . import mime` keeps working.
"""

from collections.abc import Sequence
from email.message import Message

from django.conf import settings

from apps.core.services.mail import mime as _core
from apps.core.services.mail.mime import *  # noqa: F403
from apps.core.services.mail.mime import (  # noqa: F401  (names the star import does not carry)
    Analysis,
    RecipientScan,
)

from . import documents

RECEIPT_ID_HEADER = 'X-Receipts-Receipt-Id'
SKIPPED_HEADER = 'X-Receipts-Skipped'
STRIPPED_HEADER = 'X-Receipts-Stripped'


def scan_recipients(msg: Message) -> RecipientScan:
    return _core.scan_recipients(msg, inbound_address=settings.RECEIPTS_INBOUND_ADDRESS)


def recipients_from_addresses(addresses: list[str]) -> RecipientScan:
    return _core.recipients_from_addresses(
        addresses, inbound_address=settings.RECEIPTS_INBOUND_ADDRESS
    )


def analyze(
    msg: Message, *, allowed_types: Sequence[str], max_file_bytes: int, min_inline_px: int
) -> Analysis:
    return _core.analyze(
        msg,
        allowed_types=allowed_types,
        max_file_bytes=max_file_bytes,
        min_inline_image_px=min_inline_px,
        sniff=documents.sniff_content_type,
        html_to_text=documents.html_to_text,
        image_dimensions=documents.image_dimensions,
        image_types=documents.IMAGE_TYPES,
    )


def build_stripped_eml(raw: bytes, parsed: Message, markers: dict[int, tuple[str, str]]) -> bytes:
    return _core.build_stripped_eml(
        raw,
        parsed,
        markers,
        marker_headers=(RECEIPT_ID_HEADER, SKIPPED_HEADER),
        stripped_header=STRIPPED_HEADER,
    )
