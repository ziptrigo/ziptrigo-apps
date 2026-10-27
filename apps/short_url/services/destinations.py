"""Where a short link may point, and the UTM builder.

`validate_destination` is the one check every create and every destination edit goes through (web
form, and the API later). It returns the destination in the form it is stored in: IDNA host,
percent-encoded path and query. The rules, from issue #162:

- `http` or `https` only, at most 2,048 characters, a syntactically valid host;
- an IP-literal host in a loopback, private, link-local, multicast, reserved or unspecified
  range is refused; so is a host that is not a public name (no dot, `localhost`, `.local`,
  `.internal`) and one whose last label is a number (`2130706433`, `127.1`, `0x7f.1`: a browser
  reads those as an IP address);
- no loops: a destination on this site's own `/s/` or `/go/` paths is refused, after the path has
  been percent-decoded and its dot segments resolved (`/x/../s/abc` is a loop too);
- no credentials in the URL (`https://paypal.com@evil.example/`), which only exist to deceive.

This checks what is written, not what the name resolves to: a public name can point at a private
address. The one place the server itself requests a destination, `titles.fetch_title`, resolves
the host and refuses non-public addresses on its own.
"""

import ipaddress
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ValidationError

from .errors import field_error

MAX_LENGTH = 2048
ALLOWED_SCHEMES = ('http', 'https')

#: The UTM parameters the builder knows, in the order they are appended.
UTM_PARAMS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content')

_PATH_SAFE = "/:@!$&'()*+,;=-._~%"
_QUERY_SAFE = _PATH_SAFE + '?'
_LABEL = re.compile(r'[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?')
_HEX_NUMBER = re.compile(r'0x[0-9a-f]*')
_NON_PUBLIC_SUFFIXES = ('.localhost', '.local', '.internal', '.localdomain')


def _invalid(message: str) -> ValidationError:
    return field_error('destination', message)


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # `is_global` is false for everything that is not a public unicast address (loopback,
    # private, link-local, reserved, shared 100.64/10, documentation ranges, ...); multicast is
    # global-scoped in some ranges, so it is refused on its own.
    return not ip.is_global or ip.is_multicast


def _normalize_host(host: str) -> str:
    """The lower-cased ASCII (IDNA) host, or a `ValidationError` for one that is not a public
    name or address."""
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
        try:
            ip = ipaddress.IPv6Address(host)
        except ValueError:
            raise _invalid('Enter a valid web address.') from None
        if _is_forbidden_ip(ip):
            raise _invalid('That address is not allowed.')
        return f'[{ip.compressed}]'

    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        pass
    else:
        if _is_forbidden_ip(ip):
            raise _invalid('That address is not allowed.')
        return str(ip)

    try:
        ascii_host = host.encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise _invalid('Enter a valid web address.') from None
    ascii_host = ascii_host.rstrip('.')
    labels = ascii_host.split('.')
    if len(ascii_host) > 253 or not all(_LABEL.fullmatch(label) for label in labels):
        raise _invalid('Enter a valid web address.')
    last = labels[-1]
    if (
        len(labels) < 2
        or ascii_host == 'localhost'
        or ascii_host.endswith(_NON_PUBLIC_SUFFIXES)
        or last.isdigit()
        or _HEX_NUMBER.fullmatch(last)
    ):
        raise _invalid('That address is not allowed.')
    return ascii_host


def _resolved_path(path: str) -> str:
    """`path` percent-decoded with its dot segments resolved, lower-cased, for comparing."""
    decoded = unquote(unquote(path))  # twice: `%252e%252e` is a dot segment to a lenient server
    # `lstrip`: normpath keeps a leading `//`, and a server reads `//s/abc` as `/s/abc`.
    return posixpath.normpath('/' + decoded.replace('\\', '/').lstrip('/')).lower()


def _loops_back(host: str, path: str) -> bool:
    own_host = (urlsplit(settings.BASE_URL).hostname or '').lower()
    if not own_host or host != own_host:
        return False
    resolved = _resolved_path(path)
    for prefix in (settings.SHORT_URL_PATH, settings.QR_CODE_REDIRECT_PATH):
        base = prefix.rstrip('/').lower()
        if resolved == base or resolved.startswith(base + '/'):
            return True
    return False


def destination_host(url: str) -> str:
    """The host of an already validated destination, lower-cased (what the blocked-domain check
    reads)."""
    return (urlsplit(url).hostname or '').lower()


def validate_destination(url: str) -> str:
    """The destination in its stored form, or a `ValidationError` keyed `destination`."""
    url = (url or '').strip()
    if not url:
        raise _invalid('Enter the web address to shorten.')
    if len(url) > MAX_LENGTH:
        raise _invalid(f'The address is too long (at most {MAX_LENGTH:,} characters).')
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise _invalid('The address cannot contain spaces or control characters.')

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise _invalid('Enter a valid web address.') from None
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise _invalid('The address must start with http:// or https://.')
    if not parts.netloc or not parts.hostname:
        raise _invalid('Enter a valid web address.')
    if '@' in parts.netloc:
        raise _invalid('The address cannot contain a username or password.')

    host_text = f'[{parts.hostname}]' if ':' in parts.hostname else parts.hostname
    host = _normalize_host(host_text)
    path = quote(parts.path, safe=_PATH_SAFE)
    if _loops_back(host, path):
        raise _invalid('That address already points at one of our short links.')

    netloc = host if port is None else f'{host}:{port}'
    stored = urlunsplit(
        (
            parts.scheme.lower(),
            netloc,
            path,
            quote(parts.query, safe=_QUERY_SAFE),
            quote(parts.fragment, safe=_QUERY_SAFE),
        )
    )
    if len(stored) > MAX_LENGTH:
        raise _invalid(f'The address is too long (at most {MAX_LENGTH:,} characters).')
    return stored


def apply_utm(url: str, **params: str | None) -> str:
    """`url` with the given UTM parameters merged into its query string.

    A parameter that is given (non-empty) replaces the one of the same name already in the URL;
    one that is empty or `None` leaves the URL alone, so the builder can only add or change a
    parameter: to remove one, edit the address itself. The rest of the query is kept exactly as
    it was. Only the names in `UTM_PARAMS` are applied (anything else raises `ValueError`).
    """
    unknown = set(params) - set(UTM_PARAMS)
    if unknown:
        raise ValueError(f'Unknown UTM parameter(s): {", ".join(sorted(unknown))}')
    wanted = {name: (params.get(name) or '').strip() for name in UTM_PARAMS}
    wanted = {name: value for name, value in wanted.items() if value}
    if not wanted:
        return url

    parts = urlsplit(url)
    kept = [
        piece
        for piece in parts.query.split('&')
        if piece and unquote(piece.split('=', 1)[0]).lower() not in wanted
    ]
    kept += [f'{name}={quote(value, safe="")}' for name, value in wanted.items()]
    return urlunsplit(parts._replace(query='&'.join(kept)))
