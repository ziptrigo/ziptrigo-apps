"""Reading a raw inbound email: who it is for, what SES thought of it, who sent it, which parts are
receipts, and the attachment-stripped copy we keep. Pure functions over bytes -- no database, no
S3 (`services.inbound` does the I/O and the decisions that need state).

**Header shapes** (SES receipt rule with an S3 action, `scan_enabled`; *confirmed on real staging
mail by the infra spike*, https://github.com/ziptrigo/ziptrigo-apps/issues/98#issuecomment-6124695849;
every one of them lives in this module so a different reality is a change here only):

- SES prepends its own headers, so the **topmost** occurrence of a header is SES's and anything
  below it came from the sender. We therefore read only the first `X-SES-*-Verdict` and only the
  *first* `Authentication-Results` header, and only if its authserv-id is `amazonses.com` -- never
  a later copy a sender put in their own message to look authenticated.
- The envelope recipient is in the `Received: from <host> (...) by
  inbound-smtp.<region>.amazonaws.com with SMTP id <id> for <receipts+code@...>; <date>` header
  SES added. Only the first `SES_RECEIVED_HEADER_COUNT` `Received` header(s) count, and only if
  their `by` host is SES (a sender can write any `Received` they like, even one that looks exactly
  like SES's, but it ends up below the hops that really handled the message). Without one we fall
  back to `Delivered-To`, then `X-Original-To`, then `To`/`Cc`; the first source that yields an
  inbox address wins. (A BCC and a Gmail auto-forward, where `To` is not our address, have not yet
  been seen on real mail.)
- `X-SES-Spam-Verdict` / `X-SES-Virus-Verdict` are `PASS`, `FAIL`, `GRAY` or
  `PROCESSING_FAILED`; only `FAIL` rejects. A missing one counts as not flagged.
- **SES writes no SPF/DKIM verdict headers.** `X-SES-Spf-Verdict` / `X-SES-Dkim-Verdict` and
  `Received-SPF` are ignored entirely: anyone can put them in their own message. SPF, DKIM and
  DMARC come only from the first `Authentication-Results` header, e.g. `amazonses.com; spf=pass
  (spfCheck: ...) client-ip=...; envelope-from=a@gmail.com; helo=...; dkim=pass
  header.i=@gmail.com; dmarc=pass header.from=gmail.com;` -- note that `envelope-from=` (the
  address SPF checked) is a separate `;`-segment after the `spf` result, and DKIM may carry only
  `header.i`, no `header.d`. Anything missing counts as *not authenticated* (fail closed).
- **That header echoes values the sender chose** (MAIL FROM as `envelope-from`, EHLO as `helo`),
  so it is only believed when it has exactly SES's shape (`_auth_clauses`): split on `;` outside
  quoted-strings and comments (RFC 8601, `_split_resinfo`); at most one `spf` clause, first,
  followed by its `client-ip` / `envelope-from` / `helo` (in that order); then any `dkim` clauses;
  then at most one `dmarc`; no other method, segment or property; properties separated by
  whitespace; each value in a strict grammar (`envelope-from` a dot-atom `@` LDH domain, `helo` a
  hostname or a plain IP literal, `header.i` `[dot-atom]@domain`, `header.d`/`header.from` a
  domain -- no quotes, spaces, `;` or `,`). On top of that the echoed `envelope-from` must equal
  the `Return-Path` address and `helo` the `from` host of SES's topmost `Received`, so a `;` that
  cut a value short shows up as a mismatch. If **any** check fails the whole header is dropped --
  no SPF, DKIM or DMARC credit -- and a warning with the reason (and the first 80 characters of
  the header) is logged. Raw header values are read, not RFC 2047-decoded ones.
- `Return-Path` is the envelope sender.

**Who a sender is** (`Verdicts.vouches_for`): a sender address only counts as authenticated when
the proof is about *that address*. The envelope sender (`Return-Path`) needs an SPF pass for that
address's *domain* with the `envelope-from` SES checked equal to it (SPF proves the sending IP may
send for the domain, not who wrote the local part: an exact-address entry on a large shared
provider is domain-level proof). The `From` header needs a DMARC pass whose `header.from` is
exactly the `From` domain; when SES gave no DMARC result (or `none`) a DKIM pass whose signing
domain (`header.d`, else the domain of `header.i`) equals the `From` domain or is a subdomain /
parent of it (`strictly_aligned`; no organizational-domain list). A DMARC `fail`, `temperror` or
`permerror` is final. So passing SPF/DKIM for a domain of your own does not let you pose as
somebody else's address.

Delivered mail is untrusted input, so the work is bounded before and while parsing:
`exceeds_part_limit` (MIME boundary lines in the raw bytes, checked before the parser runs),
`MAX_DEPTH` and `MAX_LEAVES` (what the walkers visit), and `build_stripped_eml`'s size limits.

The parsed tree is walked by `iter_leaves`, which descends into `multipart/*` **and**
`message/rfc822` (a message forwarded "as attachment"), so an invoice inside a forwarded email is
found like any other attachment.
"""

import copy
import html
import ipaddress
import logging
import re
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from urllib.parse import unquote, urlsplit

from . import documents
from .addresses import inbox_code_from_address, normalize_address

logger = logging.getLogger(__name__)

#: Deepest nesting we walk, counted the same way everywhere (`_walk`): every `multipart/*` and every
#: `message/rfc822` is one level. A crafted message could nest thousands deep; Python's own parser
#: limit is a `RecursionError`. Anything deeper is not visited, analysed or kept.
MAX_DEPTH = 30

#: Most leaf parts (non-container parts) `iter_leaves` yields; the rest are ignored (and emptied in
#: the stored copy). Together with `MAX_BOUNDARY_LINES` this bounds the work a message can cause.
MAX_LEAVES = 300

#: A message whose raw bytes hold more MIME boundary-like lines than this is refused before it is
#: parsed. `MAX_RAW_BYTES` alone caps bytes, not parts: a minimal part is ~10 bytes, so 40 MB could
#: be millions of `Message` objects (gigabytes, minutes of CPU). No real receipt has hundreds.
MAX_BOUNDARY_LINES = 500

#: A text part (an email body) kept in the stored copy is emptied above this many bytes, with
#: `X-Receipts-Skipped: too large`. A receipt that *is* the body is emptied regardless (it is
#: stored as its own file); this bounds the bodies that are not.
MAX_KEPT_TEXT_BYTES = 256 * 1024

#: The stored, stripped `.eml` never exceeds this many bytes: if it does after the per-part limits,
#: only the top-level headers are kept (`X-Receipts-Stripped: headers only`). The user pays storage
#: for it, so the sender must not be able to make it large.
MAX_EML_BYTES = 1024 * 1024

#: How many of the topmost `Received` headers SES itself writes (one, per the infra spike). Only
#: these are read for the envelope recipient.
SES_RECEIVED_HEADER_COUNT = 1

#: An email body with less visible text than this isn't treated as a receipt.
MIN_BODY_CHARS = 20

SES_AUTHSERV_ID = 'amazonses.com'
_SES_RECEIVED_BY = re.compile(r'\bby\s+inbound-smtp\.[\w.-]*amazonaws\.com\b', re.I)
_RECEIVED_FOR = re.compile(r'\bfor\s+(.+?)\s*(?:;|$)', re.I | re.S)
#: RFC 5322 `atext` and `dot-atom` (a mailbox local part without quotes), and an LDH hostname
#: (letters, digits, hyphens, labels of at most 63 characters). Deliberately *not* quoted-strings,
#: comments or address literals: every character these allow is inert in `Authentication-Results`
#: (none is whitespace, `;`, `,`, `"`, `(` or `)`).
_ATEXT = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]"
_DOT_ATOM = rf'{_ATEXT}+(?:\.{_ATEXT}+)*'
_LABEL = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
_DOMAIN = rf'{_LABEL}(?:\.{_LABEL})*'
#: Hosts say `_` in their EHLO name often enough that refusing it would discard the whole header of
#: legitimate mail; it is as inert as a hyphen here.
_HELO_LABEL = r'[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?'
_HELO_HOST = re.compile(rf'{_HELO_LABEL}(?:\.{_HELO_LABEL})*')
_ENVELOPE_FROM = re.compile(rf'({_DOT_ATOM})@({_DOMAIN})')
_HEADER_I = re.compile(rf'(?:{_DOT_ATOM})?@({_DOMAIN})')
_HEADER_DOMAIN = re.compile(_DOMAIN)
_DKIM_OTHER_VALUE = re.compile(r'[A-Za-z0-9._+/=-]{1,512}')
_WHITESPACE = re.compile(r'[ \t\r\n]+')
_AUTH_RESULT_PIECE = re.compile(r'([a-z][a-z0-9-]*)=([a-z]+)', re.I)
_AUTH_PROPERTY_PIECE = re.compile(r'([a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)?)=(.+)', re.I | re.S)
_RECEIVED_FROM = re.compile(r'^\s*from\s+(\S+)', re.I)
#: Longest `Authentication-Results` value we try to understand; a real one is a few hundred bytes.
MAX_AUTH_RESULTS_CHARS = 8192
#: The SPF clause's own segments, in the only order SES writes them: `spf=pass (...)
#: client-ip=...; envelope-from=...; helo=...;`.
_SPF_PROPERTIES = ('client-ip', 'envelope-from', 'helo')
_DKIM_PROPERTIES = frozenset({'header.d', 'header.i', 'header.s', 'header.a', 'header.b'})
_DMARC_PROPERTIES = frozenset({'header.from'})
#: The methods SES writes, in the order it writes them (`spf`, then any `dkim`, then `dmarc`).
_AUTH_METHODS = frozenset({'spf', 'dkim', 'dmarc'})
_BOUNDARY_LINE = re.compile(rb"^--[A-Za-z0-9'()+_,./:=?][^\r\n]{0,69}\r?$", re.M)
_CID_REFERENCE = re.compile(r"""cid:([^"'\s>)]+)""", re.I)

# -- Skip reasons (the value of `X-Receipts-Skipped` on a part that did not become a receipt) --
SKIP_EMPTY = 'empty'
SKIP_UNSUPPORTED = 'unsupported type'
SKIP_INLINE = 'inline image'
SKIP_TOO_LARGE = 'too large'
SKIP_DAILY_LIMIT = 'daily limit'
SKIP_OVER_LIMIT = 'too deep or too many parts'

RECEIPT_ID_HEADER = 'X-Receipts-Receipt-Id'
SKIPPED_HEADER = 'X-Receipts-Skipped'


def parse_message(raw: bytes) -> Message:
    """Parse with the modern `policy.default` (decoded headers, `EmailMessage` parts)."""
    return BytesParser(policy=policy.default).parsebytes(raw)


def exceeds_part_limit(raw: bytes) -> bool:
    """Whether `raw` has more MIME boundary-like lines (`--token`, optionally `--token--`) than
    `MAX_BOUNDARY_LINES` -- a cheap scan, made before the parser builds a `Message` per part.
    Lines that merely start with dashes (`-----`, a `-- ` signature) are not counted."""
    count = 0
    for _ in _BOUNDARY_LINE.finditer(raw):
        count += 1
        if count > MAX_BOUNDARY_LINES:
            return True
    return False


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
    """The `for <address>` of the `Received` header(s) SES wrote: the topmost
    `SES_RECEIVED_HEADER_COUNT`, each only if its `by` host is SES. Further down is the sender's
    side, which may say anything -- including a header that looks just like SES's and says it
    received the message for somebody else."""
    found: list[str] = []
    for value in _all(msg, 'Received')[:SES_RECEIVED_HEADER_COUNT]:
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
    #: The address SPF was checked for (`envelope-from=` of the SPF result, lowercase); empty when
    #: SES gave none (e.g. a null sender). SPF vouches for an address only if it is this one.
    spf_envelope_from: str = ''
    dkim_pass: bool = False
    #: Signing domains (`header.d`, else the domain of `header.i`) of the DKIM signatures that
    #: passed, from SES's `Authentication-Results`; empty when SES gave none.
    dkim_domains: frozenset[str] = frozenset()
    #: DMARC's result (`pass`, `fail`, `none`, ... lowercase; empty if SES gave none) and the
    #: domain it was evaluated for (`header.from=`, lowercase).
    dmarc_result: str = ''
    dmarc_from_domain: str = ''

    @property
    def dmarc_pass(self) -> bool:
        return self.dmarc_result == 'pass'

    def dkim_aligned_with(self, address: str) -> bool:
        """A DKIM signature passed *and* its domain is strictly aligned with `address`'s domain
        (`strictly_aligned`)."""
        domain = address.rpartition('@')[2].strip().lower()
        return self.dkim_pass and any(strictly_aligned(d, domain) for d in self.dkim_domains)

    def spf_vouches_for(self, address: str) -> bool:
        """SPF passed for this address's *domain* and what SES checked (`envelope-from`) is
        `address`, compared after `normalize_address` (case and `+tag`, as the allowlist does).
        Note SPF proves only that the sending IP may send for the domain, not who wrote the local
        part: an exact-address allowlist entry on a shared provider is domain-level proof."""
        if not (address and self.spf_pass and self.spf_envelope_from):
            return False
        return normalize_address(address) == normalize_address(self.spf_envelope_from)

    def from_authenticated(self, address: str) -> bool:
        """Whether the `From` address is authenticated. If SES reports a DMARC pass or fail, that
        decides: a pass counts only when its `header.from` is exactly this address's domain. Any
        other result that is not "no policy" (`temperror`, `permerror`, ...) is a failure too --
        there is no fallback from an error. With no result at all (or `none`, a domain that
        publishes no policy), a strictly aligned DKIM signature (`dkim_aligned_with`) is accepted
        instead."""
        domain = address.rpartition('@')[2].strip().lower()
        if not domain:
            return False
        if self.dmarc_result in ('', 'none'):
            return self.dkim_aligned_with(address)
        return self.dmarc_pass and self.dmarc_from_domain == domain

    def vouches_for(
        self, *, from_address: str, envelope_sender: str, is_listed: Callable[[str], bool]
    ) -> bool:
        """Whether SES authenticated a sender address that `is_listed` accepts. The proof has to be
        about the address that matched: the envelope sender needs an SPF pass *for it*; the `From`
        address needs a DMARC pass for its domain (else an aligned DKIM pass). Without either, the
        sender is unauthenticated, however many SPF/DKIM passes the message has for other
        domains."""
        if envelope_sender and is_listed(envelope_sender) and self.spf_vouches_for(envelope_sender):
            return True
        return (
            bool(from_address) and is_listed(from_address) and self.from_authenticated(from_address)
        )


def strictly_aligned(signer: str, domain: str) -> bool:
    """Whether a DKIM signing domain may vouch for a `From` domain: they are the same, or one is
    a subdomain of the other (`mail.vendor.com` signs for `vendor.com`, and `vendor.com` for
    `mail.vendor.com`). Siblings (`a.github.io` / `b.github.io`) are *not* aligned, and no list of
    organizational domains is consulted: a name that is not under the other can never be
    vouched for. Needs at least two labels on each side (never `com`)."""
    signer, domain = signer.strip().strip('.').lower(), domain.strip().strip('.').lower()
    if '.' not in signer or '.' not in domain:
        return False
    return signer == domain or domain.endswith('.' + signer) or signer.endswith('.' + domain)


@dataclass(frozen=True, slots=True)
class _AuthResults:
    spf: bool | None = None
    spf_envelope_from: str = ''
    dkim: bool | None = None
    dkim_domains: frozenset[str] = frozenset()
    dmarc_result: str = ''
    dmarc_from_domain: str = ''


class _Rejected(Exception):
    """The `Authentication-Results` header is not exactly what SES writes: nothing in it counts."""


@dataclass(slots=True)
class _Clause:
    method: str
    result: str
    properties: dict[str, str]


def _raw_all(msg: Message, name: str) -> list[str]:
    """The raw (undecoded, folded) values of `name` in order of appearance. Not
    `get_all`: that decodes RFC 2047 encoded words, so a sender-controlled value could decode
    into `;` and `=` *after* the header was written."""
    lowered = name.lower()
    values: list[str] = []
    for key, value in msg.raw_items():
        if key.lower() == lowered:
            values.append(value if isinstance(value, str) else _text(value))
    return values


def _split_resinfo(value: str) -> list[str]:
    """`value` split on `;` -- but only outside quoted-strings and comments (RFC 8601 / 5322) --
    with every comment replaced by one space. A quoted-string keeps its content (and its quotes,
    which the value grammars then refuse); a quoted-pair (`\\x`) is one unit in either; an
    unterminated quote or comment is `_Rejected`."""
    segments: list[str] = []
    buf: list[str] = []
    depth = 0
    in_quote = False
    i = 0
    while i < len(value):
        ch = value[i]
        if depth:
            if ch == '\\':
                i += 1
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if not depth:
                    buf.append(' ')
        elif in_quote:
            buf.append(ch)
            if ch == '\\':
                i += 1
                buf.append(value[i : i + 1])
            elif ch == '"':
                in_quote = False
        elif ch == '"':
            in_quote = True
            buf.append(ch)
        elif ch == '(':
            depth = 1
        elif ch == ';':
            segments.append(''.join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if depth or in_quote:
        raise _Rejected('unterminated comment or quoted-string')
    segments.append(''.join(buf))
    return segments


def _valid_ip(value: str) -> bool:
    if not re.fullmatch(r'[0-9A-Fa-f:.]+', value):
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _valid_domain(value: str) -> bool:
    return len(value) <= 253 and _HEADER_DOMAIN.fullmatch(value) is not None


def _validate_property(name: str, value: str) -> str:
    """`value` if it is a valid value of property `name` (lowercased domains); `_Rejected`
    otherwise. Strict grammars, no quoting: see the module docstring."""
    if name == 'client-ip':
        if not _valid_ip(value):
            raise _Rejected('client-ip is not an IP address')
        return value
    if name == 'envelope-from':
        match = _ENVELOPE_FROM.fullmatch(value)
        if match is None or not _valid_domain(match.group(2)) or len(value) > 254:
            raise _Rejected('envelope-from is not a plain local@domain address')
        return value.lower()
    if name == 'helo':
        if value.startswith('[') and value.endswith(']'):
            inner = value[1:-1]
            if inner[:5].lower() == 'ipv6:':
                ok = _valid_ip(inner[5:]) and ':' in inner[5:]
            else:
                ok = _valid_ip(inner) and ':' not in inner and inner.count('.') == 3
            if not ok:
                raise _Rejected('helo is not a plain IP address literal')
        elif len(value) > 253 or _HELO_HOST.fullmatch(value) is None:
            raise _Rejected('helo is not a hostname')
        return value.lower()
    if name == 'header.i':
        match = _HEADER_I.fullmatch(value)
        if match is None or not _valid_domain(match.group(1)) or len(value) > 320:
            raise _Rejected('header.i is not [local]@domain')
        return value.lower()
    if name in ('header.d', 'header.from'):
        if not _valid_domain(value):
            raise _Rejected(f'{name} is not a domain')
        return value.lower()
    if _DKIM_OTHER_VALUE.fullmatch(value) is None:
        raise _Rejected(f'{name} has an unexpected value')
    return value


def _properties(
    pieces: list[str], allowed: Sequence[str] | frozenset[str]
) -> list[tuple[str, str]]:
    """The `name=value` pieces of a segment, validated. Pieces are separated by whitespace (a
    value ends at the next whitespace), so a `,`-glued run of properties is just one bad value."""
    found: list[tuple[str, str]] = []
    for piece in pieces:
        match = _AUTH_PROPERTY_PIECE.fullmatch(piece)
        if match is None:
            raise _Rejected('a segment has something that is not a property')
        name = match.group(1).lower()
        if name not in allowed:
            raise _Rejected(f'unexpected property {name}')
        if any(name == seen for seen, _ in found):
            raise _Rejected(f'duplicate property {name}')
        found.append((name, _validate_property(name, match.group(2))))
    return found


def _auth_clauses(segments: list[str]) -> tuple[list[_Clause], dict[str, str]]:
    """Parse the `;`-segments of an `Authentication-Results` value (after its authserv-id) in the
    one shape SES writes, or raise `_Rejected`: at most one `spf` clause, first, whose
    `client-ip`, `envelope-from` and `helo` follow it (in that order, in its own segment or as
    separate `;` segments); then any number of `dkim` clauses; then at most one `dmarc`. Anything
    else -- a repeated or out-of-order method, an unknown method or property, a value outside its
    grammar -- is rejected. Returns the clauses and the SPF properties."""
    clauses: list[_Clause] = []
    spf_properties: list[tuple[str, str]] = []
    stage = 0  # 0: before/in the spf group, 1: dkim, 2: dmarc seen
    for segment in segments:
        if re.search(r'[^\x20-\x7e\t\r\n]', segment):
            raise _Rejected('non-ASCII or control characters')
        pieces = [piece for piece in _WHITESPACE.split(segment.strip()) if piece]
        if not pieces:
            raise _Rejected('an empty segment')
        head = _AUTH_RESULT_PIECE.fullmatch(pieces[0])
        if head is not None and head.group(1).lower() in _AUTH_METHODS:
            method, result = head.group(1).lower(), head.group(2).lower()
            if method == 'spf':
                if clauses or stage:
                    raise _Rejected('spf is repeated or not first')
                properties = _properties(pieces[1:], _SPF_PROPERTIES)
                spf_properties.extend(properties)
            elif method == 'dkim':
                if stage == 2:
                    raise _Rejected('dkim after dmarc')
                stage = 1
                properties = _properties(pieces[1:], _DKIM_PROPERTIES)
            else:
                if stage == 2:
                    raise _Rejected('dmarc is repeated')
                stage = 2
                properties = _properties(pieces[1:], _DMARC_PROPERTIES)
            clauses.append(_Clause(method, result, dict(properties)))
        elif clauses and clauses[-1].method == 'spf' and stage == 0:
            spf_properties.extend(_properties(pieces, _SPF_PROPERTIES))
        else:
            raise _Rejected('a segment that is not a known method clause')
        names = [name for name, _ in spf_properties]
        if names != sorted(set(names), key=_SPF_PROPERTIES.index):
            raise _Rejected('spf properties repeated or out of order')
    return clauses, dict(spf_properties)


def _received_from_host(msg: Message) -> str:
    """The `from <host>` token of the topmost `Received` header, which must be SES's own (its `by`
    host is SES), lowercased; empty otherwise."""
    values = _raw_all(msg, 'Received')[:SES_RECEIVED_HEADER_COUNT]
    if not values or not _SES_RECEIVED_BY.search(values[0]):
        return ''
    match = _RECEIVED_FROM.match(values[0])
    return match.group(1).lower() if match else ''


def _raw_return_path(msg: Message) -> str:
    values = _raw_all(msg, 'Return-Path')
    match = re.fullmatch(r'\s*<([^<>]*)>\s*|\s*([^<>\s]*)\s*', values[0]) if values else None
    return ((match.group(1) or match.group(2) or '') if match else '').strip().lower()


def _check_against_headers(msg: Message, spf_properties: dict[str, str]) -> None:
    """The echoed MAIL FROM and EHLO must be the very ones SES wrote elsewhere. A `;` in either
    would have cut the echo short in `Authentication-Results`, so what is left no longer matches
    the full value in `Return-Path` / `Received`."""
    envelope_from = spf_properties.get('envelope-from')
    if envelope_from is not None:
        decoded = envelope_sender(msg).lower()
        if envelope_from != _raw_return_path(msg) or envelope_from != decoded:
            raise _Rejected('envelope-from is not the Return-Path address')
    helo = spf_properties.get('helo')
    if helo is not None and helo != _received_from_host(msg):
        raise _Rejected("helo is not the host in SES's Received header")


def _parse_ses_authentication_results(msg: Message, value: str) -> _AuthResults:
    if len(value) > MAX_AUTH_RESULTS_CHARS:
        raise _Rejected('too long')
    segments = _split_resinfo(value)
    if segments[0].strip().lower() != SES_AUTHSERV_ID:
        raise _Rejected('the first Authentication-Results is not from amazonses.com')
    segments = segments[1:]
    if segments and not segments[-1].strip():
        segments.pop()  # SES ends the header with `;`
    if not segments or [segment.strip().lower() for segment in segments] == ['none']:
        return _AuthResults()
    clauses, spf_properties = _auth_clauses(segments)
    _check_against_headers(msg, spf_properties)

    spf = next((c for c in clauses if c.method == 'spf'), None)
    dmarc = next((c for c in clauses if c.method == 'dmarc'), None)
    dkim: bool | None = None
    domains: set[str] = set()
    for clause in clauses:
        if clause.method != 'dkim':
            continue
        passed = clause.result == 'pass'
        dkim = bool(dkim) or passed
        if passed:
            if 'header.d' in clause.properties:
                domains.add(clause.properties['header.d'])
            elif 'header.i' in clause.properties:
                domains.add(clause.properties['header.i'].rpartition('@')[2])
    return _AuthResults(
        spf=None if spf is None else spf.result == 'pass',
        spf_envelope_from=spf_properties.get('envelope-from', ''),
        dkim=dkim,
        dkim_domains=frozenset(domains),
        dmarc_result='' if dmarc is None else dmarc.result,
        dmarc_from_domain='' if dmarc is None else dmarc.properties.get('header.from', ''),
    )


def _ses_authentication_results(msg: Message) -> _AuthResults:
    """What the **first** `Authentication-Results` header says, if SES wrote it (authserv-id
    `amazonses.com`); nothing otherwise. Later copies are the sender's. The header carries values
    the sender chose (MAIL FROM, EHLO), so it is accepted only if it has exactly SES's shape
    (`_auth_clauses`), every value matches a strict grammar and the echoed MAIL FROM / EHLO match
    `Return-Path` / `Received`; if any check fails the whole header is dropped and no SPF, DKIM or
    DMARC credit is given (a warning is logged with the reason, never the header)."""
    values = _raw_all(msg, 'Authentication-Results')
    if not values:
        return _AuthResults()
    try:
        return _parse_ses_authentication_results(msg, values[0])
    except _Rejected as exc:
        logger.warning(
            'Authentication-Results not trusted (%s); no SPF/DKIM/DMARC credit. Starts with: %s',
            exc,
            clean_text(values[0], 80),
        )
        return _AuthResults()


def read_verdicts(msg: Message) -> Verdicts:
    """What SES concluded. Spam/virus come from the topmost `X-SES-Spam-Verdict` /
    `X-SES-Virus-Verdict`; SPF, DKIM and DMARC *only* from SES's `Authentication-Results`. The
    `X-SES-Spf-Verdict`, `X-SES-Dkim-Verdict` and `Received-SPF` headers are deliberately not read:
    SES never writes them, so any copy is the sender's."""
    spam = _first(msg, 'X-SES-Spam-Verdict').upper()
    virus = _first(msg, 'X-SES-Virus-Verdict').upper()
    results = _ses_authentication_results(msg)
    return Verdicts(
        spam_failed=spam == 'FAIL',
        virus_failed=virus == 'FAIL',
        spf_pass=bool(results.spf),
        spf_envelope_from=results.spf_envelope_from,
        dkim_pass=bool(results.dkim),
        dkim_domains=results.dkim_domains,
        dmarc_result=results.dmarc_result,
        dmarc_from_domain=results.dmarc_from_domain,
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
#: receipts) -- but only when SES authenticated the sender *as that address* (`Verdicts.vouches_for`:
#: DMARC for `google.com`, an aligned DKIM signature, or SPF on the envelope sender), and the email
#: only ever becomes a free `forwarding_confirmation` row, never a receipt.
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
        try:
            host = urlsplit(candidate).hostname
        except ValueError:  # e.g. `https://[x`: a malformed link is skipped, not a crash
            continue
        if host in FORWARDING_LINK_HOSTS:
            link = candidate[:2000]
            break
    return code, link


# -- The tree -----------------------------------------------------------------------------------


def _children(part: Message) -> list[Message]:
    payload = part.get_payload() if part.is_multipart() else []
    return [child for child in payload if isinstance(child, Message)]


def _walk(
    node: Message, *, into_messages: bool = True, _depth: int = 0, _parent: Message | None = None
) -> Iterator[tuple[Message, Message | None]]:
    """Every node (containers and leaves) in document order, each with its parent. The one depth
    measure for every walker: each `multipart/*` and each `message/rfc822` is one level, and a
    node deeper than `MAX_DEPTH` (with everything under it) is not visited. With `into_messages`
    false a `message/rfc822` is not descended into."""
    if _depth > MAX_DEPTH:
        return
    yield node, _parent
    if node.is_multipart() and (into_messages or node.get_content_type() != 'message/rfc822'):
        for child in _children(node):
            yield from _walk(child, into_messages=into_messages, _depth=_depth + 1, _parent=node)


def iter_leaves(msg: Message, *, into_messages: bool = True) -> Iterator[Message]:
    """Every non-container part in document order, at most `MAX_LEAVES` of them. Containers are
    `multipart/*` and, when `into_messages` is true, `message/rfc822` (whose inner message's parts
    are yielded too)."""
    count = 0
    for node, _ in _walk(msg, into_messages=into_messages):
        if node.is_multipart():
            continue
        if count >= MAX_LEAVES:
            return
        count += 1
        yield node


def iter_messages(msg: Message) -> Iterator[Message]:
    """`msg` and every message nested in it as `message/rfc822`, outermost first (the same walk,
    so the same depth limit, as `iter_leaves`)."""
    yield msg
    for node, parent in _walk(msg):
        if parent is not None and parent.get_content_type() == 'message/rfc822':
            yield node


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
    #: The decoded payload -- only kept for an eligible part; a skipped one is empty (its `size`
    #: is all that is needed), so a message full of unusable parts doesn't stay in memory.
    data: bytes
    size: int = 0
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
            index = index_of.get(id(part))
            if index is None:  # deeper than the main walk goes: not a part we track
                continue
            visible = len(_visible_text(part))
            if visible < MIN_BODY_CHARS:
                continue
            info = BodyInfo(
                index=index,
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
    logo or a signature and is skipped; the body is only chosen as a fallback (so `Analysis.body`
    is `None` whenever an attachment is eligible)."""
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
            size=len(data),
        )
        if not data:
            info.skip = SKIP_EMPTY
        elif sniffed is None or sniffed not in allowed_types:
            info.skip = SKIP_UNSUPPORTED
        elif _is_tiny_inline_image(leaf, data, sniffed, cids, min_inline_px):
            info.skip = SKIP_INLINE
        elif len(data) > max_file_bytes:
            info.skip = SKIP_TOO_LARGE
        if info.skip:
            info.data = b''
        attachments.append(info)
    # The body is only a fallback: with an eligible attachment it is never looked at.
    eligible = any(not info.skip for info in attachments)
    return Analysis(attachments=attachments, body=None if eligible else _choose_body(msg, index_of))


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


def _payload_size(part: Message) -> int:
    payload = part.get_payload()
    return len(payload) if isinstance(payload, str | bytes) else 0


def _empty(part: Message, header: str, value: str) -> None:
    part.set_payload('')
    for name in (RECEIPT_ID_HEADER, SKIPPED_HEADER):  # one the sender wrote must not survive
        del part[name]
    part[header] = value


def build_stripped_eml(raw: bytes, parsed: Message, markers: dict[int, tuple[str, str]]) -> bytes:
    """The email as we keep it: every part in `markers` (leaf index -> `(header, value)`, from
    `X-Receipts-Receipt-Id` / `X-Receipts-Skipped`) has its payload replaced by an empty body and
    that header added, so the file it became (or would have) is not stored twice and the
    original structure and headers survive. Works on a deep copy of `parsed` (the tree the
    analysis used, parsed once), so one message can be stripped differently per recipient.

    The result is bounded, because the user pays to store it and the sender chooses what is in
    it: any other text part over `MAX_KEPT_TEXT_BYTES`, any leaf past `MAX_LEAVES` and anything
    deeper than `MAX_DEPTH` is emptied too (`X-Receipts-Skipped`), and if the whole is still over
    `MAX_EML_BYTES` -- or the message can't be serialized again -- only its top-level headers are
    kept (`raw` is where they come from)."""
    try:
        msg = copy.deepcopy(parsed)
        nodes = list(_walk(msg))
        visited = {id(node) for node, _ in nodes}
        leaf_index = 0
        for node, _ in nodes:
            if node.is_multipart():
                # Children the walk never reached (too deep) are dropped.
                if any(id(child) not in visited for child in _children(node)):
                    node.set_payload('')
                continue
            index, leaf_index = leaf_index, leaf_index + 1
            marker = markers.get(index)
            if index >= MAX_LEAVES:
                _empty(node, SKIPPED_HEADER, SKIP_OVER_LIMIT)
            elif marker is not None:
                _empty(node, *marker)
            elif _is_text(node) and _payload_size(node) > MAX_KEPT_TEXT_BYTES:
                _empty(node, SKIPPED_HEADER, SKIP_TOO_LARGE)
        eml = msg.as_bytes(policy=policy.default.clone(refold_source='none'))
        if len(eml) <= MAX_EML_BYTES:
            return eml
        logger.info(
            'Stripped email is %s bytes (over %s); keeping its headers only',
            len(eml),
            MAX_EML_BYTES,
        )
    except Exception:
        logger.exception('Could not rebuild the stripped email; keeping its headers only')
    return _headers_only(raw)


def _headers_only(raw: bytes) -> bytes:
    match = re.search(rb'\r?\n\r?\n', raw)
    head = raw[: match.start()] if match else raw
    return head[:MAX_EML_BYTES] + b'\nX-Receipts-Stripped: headers only\n'
