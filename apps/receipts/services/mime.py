"""Reading a raw inbound email: who it is for, what SES thought of it, who sent it, which parts are
receipts, and the attachment-stripped copy we keep. Pure functions over bytes -- no database, no
S3 (`services.inbound` does the I/O and the decisions that need state).

**Header assumptions** (SES receipt rule with an S3 action, `scan_enabled`; *still to be confirmed
by the infra spike in joaonc/infra#373* -- every one of them lives in this module so a different
reality is a change here only):

- SES prepends its own headers, so the **topmost** occurrence of a header is SES's and anything
  below it came from the sender. We therefore read only the first `X-SES-*-Verdict` and only the
  first `Authentication-Results` whose authserv-id is `amazonses.com`, never a later copy a sender
  put in their own message to look authenticated.
- The envelope recipient is in a `Received: ... by inbound-smtp.<region>.amazonaws.com ... for
  <receipts+code@...>;` header SES added. Only the leading run of `Received` headers whose `by`
  host is SES counts (a sender can write any `Received` they like, but it ends up below the hops
  that really handled the message). Without one we fall back to `Delivered-To`, then
  `X-Original-To`, then `To`/`Cc`; the first source that yields an inbox address wins.
- `X-SES-Spam-Verdict` / `X-SES-Virus-Verdict` are `PASS`, `FAIL`, `GRAY` or
  `PROCESSING_FAILED`; only `FAIL` rejects. `X-SES-Spf-Verdict` / `X-SES-Dkim-Verdict` are `PASS`
  or not; when absent, `Authentication-Results: amazonses.com; spf=pass ...; dkim=pass ...` is read
  instead, then `Received-SPF`. A missing verdict counts as *not authenticated* (fail closed),
  while a missing spam/virus verdict counts as not flagged.
- `Return-Path` is the envelope sender.

The parsed tree is walked by `iter_leaves`, which descends into `multipart/*` **and**
`message/rfc822` (a message forwarded "as attachment"), so an invoice inside a forwarded email is
found like any other attachment.
"""

import html
import logging
import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from urllib.parse import unquote, urlsplit

from . import documents
from .addresses import inbox_code_from_address

logger = logging.getLogger(__name__)

#: Deepest nesting of multiparts / forwarded messages we walk (a crafted message could nest
#: thousands deep; Python's own parser limit is a `RecursionError`).
MAX_DEPTH = 30

#: An email body with less visible text than this isn't treated as a receipt.
MIN_BODY_CHARS = 20

SES_AUTHSERV_ID = 'amazonses.com'
_SES_RECEIVED_BY = re.compile(r'\bby\s+inbound-smtp\.[\w.-]*amazonaws\.com\b', re.I)
_RECEIVED_FOR = re.compile(r'\bfor\s+(.+?)\s*(?:;|$)', re.I | re.S)
_AUTH_RESULT = re.compile(r'\b(spf|dkim)\s*=\s*([a-z]+)', re.I)
_COMMENT = re.compile(r'\([^()]*\)')
_CID_REFERENCE = re.compile(r"""cid:([^"'\s>)]+)""", re.I)

# -- Skip reasons (the value of `X-Receipts-Skipped` on a part that did not become a receipt) --
SKIP_EMPTY = 'empty'
SKIP_UNSUPPORTED = 'unsupported type'
SKIP_INLINE = 'inline image'
SKIP_TOO_LARGE = 'too large'
SKIP_DAILY_LIMIT = 'daily limit'

RECEIPT_ID_HEADER = 'X-Receipts-Receipt-Id'
SKIPPED_HEADER = 'X-Receipts-Skipped'


def parse_message(raw: bytes) -> Message:
    """Parse with the modern `policy.default` (decoded headers, `EmailMessage` parts)."""
    return BytesParser(policy=policy.default).parsebytes(raw)


def _text(value: object) -> str:
    try:
        return str(value)
    except Exception:  # a header the parser couldn't make sense of
        return ''


def _all(msg: Message, name: str) -> list[str]:
    return [_text(value) for value in (msg.get_all(name) or [])]


def _first(msg: Message, name: str) -> str:
    values = _all(msg, name)
    return values[0].strip() if values else ''


def clean_text(value: str, limit: int) -> str:
    """A header value made safe to store and show: control characters dropped, truncated."""
    # Bytes the parser couldn't decode survive as lone surrogates, which the database refuses.
    value = value.encode('utf-8', 'replace').decode('utf-8')
    cleaned = ''.join(' ' if ch in '\r\n\t' else ch for ch in value)
    cleaned = ''.join(ch for ch in cleaned if unicodedata.category(ch) != 'Cc')
    return cleaned.strip()[:limit]


# -- Recipients ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InboxRecipient:
    code: str
    #: The address we canonicalise to and store (`receipts+<code>@<domain>`, lowercase).
    address: str


@dataclass(slots=True)
class RecipientScan:
    matches: list[InboxRecipient]
    #: Every address looked at in the source that was used (or the last one tried), for the log
    #: line when nothing matched.
    seen: list[str]


def _received_for_addresses(msg: Message) -> list[str]:
    """The `for <address>` of the `Received` headers SES wrote -- the leading run of `Received`
    headers whose `by` host is SES. The run stops at the first one that isn't: a header further
    down was written by the sender's side, and may say anything (including that it was received by
    SES for somebody else)."""
    found: list[str] = []
    for value in _all(msg, 'Received'):
        if not _SES_RECEIVED_BY.search(value):
            break
        match = _RECEIVED_FOR.search(value)
        if match:
            found.extend(addr for _, addr in getaddresses([match.group(1)]) if addr)
    return found


def _header_addresses(msg: Message, *names: str) -> list[str]:
    values = [value for name in names for value in _all(msg, name)]
    return [addr for _, addr in getaddresses(values) if addr]


def scan_recipients(msg: Message) -> RecipientScan:
    """The inbox addresses (`receipts+<code>@domain`) this message was delivered to.

    Sources, in order (the first that yields any inbox address wins): the envelope recipient(s) in
    SES's `Received ... for` header, `Delivered-To`, `X-Original-To`, `To` and `Cc`. `To` alone
    would be wrong -- a Gmail auto-forward or a BCC has some other address there -- which is why
    the envelope comes first."""
    sources = (
        _received_for_addresses(msg),
        _header_addresses(msg, 'Delivered-To'),
        _header_addresses(msg, 'X-Original-To'),
        _header_addresses(msg, 'To', 'Cc'),
    )
    seen: list[str] = []
    for addresses in sources:
        seen = addresses
        matches: dict[str, InboxRecipient] = {}
        for address in addresses:
            code = inbox_code_from_address(address)
            if code is not None and code not in matches:
                matches[code] = InboxRecipient(code=code, address=_canonical_address(code))
        if matches:
            return RecipientScan(matches=list(matches.values()), seen=addresses)
    return RecipientScan(matches=[], seen=seen)


def recipients_from_addresses(addresses: list[str]) -> RecipientScan:
    """Like `scan_recipients` for addresses given explicitly (the `--to` of the management
    command)."""
    matches: dict[str, InboxRecipient] = {}
    for address in addresses:
        code = inbox_code_from_address(address)
        if code is not None and code not in matches:
            matches[code] = InboxRecipient(code=code, address=_canonical_address(code))
    return RecipientScan(matches=list(matches.values()), seen=list(addresses))


def _canonical_address(code: str) -> str:
    from .accounts import inbox_address

    return inbox_address(code)


# -- SES verdicts -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verdicts:
    spam_failed: bool = False
    virus_failed: bool = False
    spf_pass: bool = False
    dkim_pass: bool = False

    @property
    def authenticated(self) -> bool:
        """SES vouches for the sender: SPF *or* DKIM passed."""
        return self.spf_pass or self.dkim_pass


def _ses_authentication_results(msg: Message) -> dict[str, bool]:
    """`{'spf': passed, 'dkim': passed}` from the topmost `Authentication-Results` SES wrote (its
    authserv-id is `amazonses.com`); empty when there is none. A result is `True` if any instance
    of the method passed (a message can carry several DKIM signatures)."""
    for value in _all(msg, 'Authentication-Results'):
        authserv_id, _, results = value.partition(';')
        if authserv_id.strip().lower() != SES_AUTHSERV_ID:
            continue
        parsed: dict[str, bool] = {}
        for method, result in _AUTH_RESULT.findall(_COMMENT.sub(' ', results)):
            parsed[method.lower()] = parsed.get(method.lower(), False) or result.lower() == 'pass'
        return parsed
    return {}


def read_verdicts(msg: Message) -> Verdicts:
    spam = _first(msg, 'X-SES-Spam-Verdict').upper()
    virus = _first(msg, 'X-SES-Virus-Verdict').upper()
    spf_header = _first(msg, 'X-SES-Spf-Verdict').upper()
    dkim_header = _first(msg, 'X-SES-Dkim-Verdict').upper()

    results = _ses_authentication_results(msg) if not (spf_header and dkim_header) else {}
    if spf_header:
        spf_pass = spf_header == 'PASS'
    elif 'spf' in results:
        spf_pass = results['spf']
    else:
        spf_pass = _first(msg, 'Received-SPF').lower().startswith('pass')
    if dkim_header:
        dkim_pass = dkim_header == 'PASS'
    else:
        dkim_pass = results.get('dkim', False)
    return Verdicts(
        spam_failed=spam == 'FAIL',
        virus_failed=virus == 'FAIL',
        spf_pass=spf_pass,
        dkim_pass=dkim_pass,
    )


# -- Senders ------------------------------------------------------------------------------------


def from_address(msg: Message) -> str:
    for _, address in getaddresses(_all(msg, 'From')):
        if address:
            return address.strip()
    return ''


def envelope_sender(msg: Message) -> str:
    """The `Return-Path` address (empty for the null sender `<>`)."""
    return parseaddr(_first(msg, 'Return-Path'))[1].strip()


def subject(msg: Message) -> str:
    return clean_text(_first(msg, 'Subject'), 500)


def received_at(msg: Message) -> datetime:
    """When SES got it (the date of the topmost `Received` header), else the `Date` header, else
    now. Never in the future: a bad clock must not put an email at the top of a list forever."""
    now = datetime.now(UTC)
    candidates = [*_all(msg, 'Received')[:1], _first(msg, 'Date')]
    for value in candidates:
        raw = value.rpartition(';')[2] if ';' in value else value
        try:
            parsed = parsedate_to_datetime(raw.strip())
        except TypeError, ValueError, IndexError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return min(parsed, now + timedelta(minutes=5))
    return now


# -- Forwarding confirmations -------------------------------------------------------------------

#: Senders of a "confirm this forwarding address" email. These skip the sender allowlist (the
#: account owner has to be able to complete the setup of the very forwarding that delivers their
#: receipts) -- but only when SES authenticated the sender, and the email only ever becomes a free
#: `forwarding_confirmation` row, never a receipt.
#:
#: Gmail: `forwarding-noreply@google.com` (the older `mail-noreply@google.com` is no longer used).
#: Outlook.com / Microsoft 365 forwarding needs no confirmation message from the forwarding side,
#: so there is nothing to match for it; add the sender here if one turns up.
FORWARDING_CONFIRMATION_SENDERS = frozenset({'forwarding-noreply@google.com'})

#: The only hosts a confirmation link may point at. The link is shown on the settings page, so an
#: email that merely *claims* to be Google's must not be able to plant an arbitrary URL there.
FORWARDING_LINK_HOSTS = frozenset({'mail-settings.google.com'})

_CONFIRMATION_CODE = re.compile(r'confirmation\s+code\s*:?\s*(\d{4,12})', re.I)
_SUBJECT_CODE = re.compile(r'\(#(\d{4,12})\)')
_URL = re.compile(r'https://[^\s<>"\'\])]+', re.I)


def is_forwarding_confirmation_sender(*addresses: str) -> bool:
    from .addresses import normalize_address

    return any(normalize_address(a) in FORWARDING_CONFIRMATION_SENDERS for a in addresses if a)


def extract_forwarding_confirmation(msg: Message) -> tuple[str, str]:
    """`(code, link)` from a forwarding-confirmation email; either may be empty."""
    blob = html.unescape('\n'.join(part_text(leaf) for leaf in iter_leaves(msg) if _is_text(leaf)))
    code = ''
    match = _CONFIRMATION_CODE.search(blob) or _SUBJECT_CODE.search(_first(msg, 'Subject'))
    if match:
        code = match.group(1)
    link = ''
    for candidate in _URL.findall(blob):
        candidate = candidate.rstrip('.,;')
        if urlsplit(candidate).hostname in FORWARDING_LINK_HOSTS:
            link = candidate[:2000]
            break
    return code, link


# -- The tree -----------------------------------------------------------------------------------


def _children(part: Message) -> list[Message]:
    payload = part.get_payload() if part.is_multipart() else []
    return [child for child in payload if isinstance(child, Message)]


def iter_leaves(msg: Message, *, into_messages: bool = True, _depth: int = 0) -> Iterator[Message]:
    """Every non-container part in document order. Containers are `multipart/*` and, when
    `into_messages` is true, `message/rfc822` (whose inner message's parts are yielded too)."""
    if _depth > MAX_DEPTH:
        return
    if msg.is_multipart() and (into_messages or msg.get_content_type() != 'message/rfc822'):
        for child in _children(msg):
            yield from iter_leaves(child, into_messages=into_messages, _depth=_depth + 1)
    elif not msg.is_multipart():
        yield msg


def _nested_messages(part: Message, depth: int) -> Iterator[Message]:
    if depth > MAX_DEPTH:
        return
    for child in _children(part):
        if child.get_content_type() == 'message/rfc822':
            for inner in _children(child):
                yield inner
                yield from _nested_messages(inner, depth + 1)
        else:
            yield from _nested_messages(child, depth + 1)


def iter_messages(msg: Message) -> Iterator[Message]:
    """`msg` and every message nested in it as `message/rfc822`, outermost first."""
    yield msg
    yield from _nested_messages(msg, 0)


def _is_text(part: Message) -> bool:
    return part.get_content_type() in ('text/plain', 'text/html')


def is_attachment_like(part: Message) -> bool:
    """Anything that isn't plain message text: a file, an image, an inline picture."""
    if _is_text(part):
        return part.get_content_disposition() == 'attachment' or bool(part.get_filename())
    return True


def part_text(part: Message) -> str:
    """The decoded text of a `text/*` part (its declared charset, replacing what won't decode)."""
    try:
        content = part.get_content()  # ty: ignore[unresolved-attribute]
        if isinstance(content, str):
            return content
    except Exception:
        pass
    payload = part.get_payload(decode=True)
    return payload.decode('utf-8', 'replace') if isinstance(payload, bytes) else ''


def part_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, bytes) else b''


def _visible_text(part: Message) -> str:
    text = part_text(part)
    if part.get_content_type() == 'text/html':
        return documents.html_to_text(text)
    return text.strip()


# -- What in the message is a receipt -----------------------------------------------------------


@dataclass(slots=True)
class AttachmentInfo:
    """One attachment-like part and what we decided about it. `skip` is empty when it is eligible
    to become a receipt, else one of the `SKIP_*` reasons."""

    index: int
    filename: str
    content_type: str
    data: bytes
    skip: str = ''


@dataclass(slots=True)
class BodyInfo:
    """The email body chosen as the receipt when there is no eligible attachment."""

    index: int
    content_type: str  # text/html or text/plain
    data: bytes  # UTF-8


@dataclass(slots=True)
class Analysis:
    attachments: list[AttachmentInfo]
    body: BodyInfo | None

    @property
    def eligible(self) -> list[AttachmentInfo]:
        return [item for item in self.attachments if not item.skip]

    @property
    def too_large(self) -> list[AttachmentInfo]:
        return [item for item in self.attachments if item.skip == SKIP_TOO_LARGE]


def _cid_references(msg: Message) -> set[str]:
    """The Content-IDs every HTML part of the message refers to with `cid:`."""
    cids: set[str] = set()
    for leaf in iter_leaves(msg):
        if leaf.get_content_type() == 'text/html':
            for match in _CID_REFERENCE.findall(part_text(leaf)):
                cids.add(unquote(html.unescape(match)).strip().strip('<>').lower())
    return cids


def _content_id(part: Message) -> str:
    return _text(part.get('Content-ID') or '').strip().strip('<>').lower()


def _choose_body(msg: Message, index_of: dict[int, int]) -> BodyInfo | None:
    """HTML preferred over text; for a message that carries other messages (forwarded "as
    attachment"), whichever message has the most visible text -- the cover note ("see attached")
    loses to the forwarded email it came with."""
    best: tuple[int, BodyInfo] | None = None
    for candidate in iter_messages(msg):
        leaves = [
            p
            for p in iter_leaves(candidate, into_messages=False)
            if _is_text(p) and not is_attachment_like(p)
        ]
        for content_type in ('text/html', 'text/plain'):
            part = next((p for p in leaves if p.get_content_type() == content_type), None)
            if part is None:
                continue
            visible = len(_visible_text(part))
            if visible < MIN_BODY_CHARS:
                continue
            info = BodyInfo(
                index=index_of[id(part)],
                content_type=content_type,
                data=part_text(part).encode('utf-8', 'replace'),
            )
            if best is None or visible > best[0]:
                best = (visible, info)
            break
    return best[1] if best else None


def analyze(
    msg: Message, *, allowed_types: Sequence[str], max_file_bytes: int, min_inline_px: int
) -> Analysis:
    """Decide, part by part, what could become a receipt (see the module docstring and
    `services.inbound`): an attachment is eligible when its **sniffed** type (the magic bytes --
    never the declared type or the file name) is an allowed one and it fits `max_file_bytes`;
    an image referenced from the HTML body by `cid:` with both sides under `min_inline_px` is a
    logo or a signature and is skipped; the body is only chosen as a fallback."""
    leaves = list(iter_leaves(msg))
    index_of = {id(leaf): i for i, leaf in enumerate(leaves)}
    cids = _cid_references(msg)

    attachments: list[AttachmentInfo] = []
    for index, leaf in enumerate(leaves):
        if not is_attachment_like(leaf):
            continue
        data = part_bytes(leaf)
        sniffed = documents.sniff_content_type(data) if data else None
        info = AttachmentInfo(
            index=index,
            filename=_text(leaf.get_filename() or ''),
            content_type=sniffed or '',
            data=data,
        )
        if not data:
            info.skip = SKIP_EMPTY
        elif sniffed is None or sniffed not in allowed_types:
            info.skip = SKIP_UNSUPPORTED
        elif _is_tiny_inline_image(leaf, data, sniffed, cids, min_inline_px):
            info.skip = SKIP_INLINE
        elif len(data) > max_file_bytes:
            info.skip = SKIP_TOO_LARGE
        attachments.append(info)
    return Analysis(attachments=attachments, body=_choose_body(msg, index_of))


def _is_tiny_inline_image(
    part: Message, data: bytes, content_type: str, cids: set[str], min_px: int
) -> bool:
    if content_type not in documents.IMAGE_TYPES:
        return False
    cid = _content_id(part)
    if not cid or cid not in cids:
        return False
    size = documents.image_dimensions(data)
    # An image whose size can't be read isn't assumed to be decoration: processing will say so.
    return size is not None and max(size) < min_px


# -- The stripped copy --------------------------------------------------------------------------


def build_stripped_eml(raw: bytes, markers: dict[int, tuple[str, str]]) -> bytes:
    """The email as we keep it: every part in `markers` (leaf index -> `(header, value)`, from
    `X-Receipts-Receipt-Id` / `X-Receipts-Skipped`) has its payload replaced by an empty body and
    that header added, so the file it became (or would have) is not stored twice and the
    original structure and headers survive. Re-parses `raw` rather than mutating the tree used
    for the analysis, so one message can be stripped differently per recipient.

    If the message can't be serialized again, only its top-level headers are kept."""
    try:
        msg = parse_message(raw)
        for index, leaf in enumerate(iter_leaves(msg)):
            marker = markers.get(index)
            if marker is None:
                continue
            leaf.set_payload('')
            leaf[marker[0]] = marker[1]
        return msg.as_bytes(policy=policy.default.clone(refold_source='none'))
    except Exception:
        logger.exception('Could not rebuild the stripped email; keeping its headers only')
        head = re.split(rb'\r?\n\r?\n', raw, maxsplit=1)[0]
        return head + b'\nX-Receipts-Stripped: headers only\n'
