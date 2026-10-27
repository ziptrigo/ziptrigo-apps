"""Fetching a page's `<title>` for a new link.

This is the one place the server requests a URL a user chose, so it is guarded against SSRF
(issue #162): it runs as a background task (never in a web request), and

- every host is resolved first and refused unless **all** its addresses are public; the request
  then goes to the address that was checked (not a second lookup, which could answer differently:
  DNS rebinding), with the host name still used for the `Host` header and TLS verification;
- the same is done again after every redirect, at most 3 of them, and a redirect target goes
  through `validate_destination` like any other address;
- 5 seconds in total, at most 256 KB read, only a `text/html` answer is parsed, with no
  compression asked for or accepted, and the title is cut to 255 characters.

Any failure (a refusal, a timeout, an odd answer) just leaves the title empty. Nothing about the
destination is logged except the link's id.
"""

import http.client
import ipaddress
import logging
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.tasks import task

from ..models import ShortLink
from .destinations import validate_destination

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 5.0
MAX_BODY_BYTES = 256 * 1024
MAX_TITLE_LENGTH = 255


class UnsafeDestination(Exception):
    """The host resolves to (or is) a non-public address."""


@dataclass(slots=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


def resolve_public(host: str, port: int) -> str:
    """The IP address to connect to for `host`, which must resolve only to public addresses.
    Raises `UnsafeDestination` otherwise (including when it doesn't resolve)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise UnsafeDestination('does not resolve') from None
    addresses = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split('%')[0])
        except ValueError:
            raise UnsafeDestination('unreadable address') from None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise UnsafeDestination('not a public address')
        addresses.append(str(ip))
    if not addresses:
        raise UnsafeDestination('does not resolve')
    return addresses[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        # `server_hostname` is the real host: the certificate must be valid for it.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # ty: ignore[unresolved-attribute]


def _abort(connection: http.client.HTTPConnection) -> None:
    """Cut a connection that is still open at the deadline: whatever is blocked reading it
    returns at once."""
    sock = connection.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def http_get(url: str, ip: str, deadline: float) -> Response:
    """One GET of `url` to the address `ip`, reading at most `MAX_BODY_BYTES` of the body and
    giving up at `deadline` (a `time.monotonic()` value) however slowly the server answers: a
    timer cuts the connection then, so a server that drips a byte at a time can't hold the
    worker. Raises on any network error, and `TimeoutError` past the deadline."""
    parts = urlsplit(url)
    https = parts.scheme == 'https'
    port = parts.port or (443 if https else 80)
    host = parts.hostname or ''
    remaining = max(deadline - time.monotonic(), 0.1)
    connection_class = _PinnedHTTPSConnection if https else _PinnedHTTPConnection
    connection = connection_class(host, port, ip, remaining)
    watchdog = threading.Timer(remaining, _abort, args=(connection,))
    watchdog.daemon = True
    watchdog.start()
    try:
        target = parts.path or '/'
        if parts.query:
            target += '?' + parts.query
        connection.request(
            'GET',
            target,
            headers={
                'Host': parts.netloc,
                'User-Agent': f'ZipTrigoLinkTitleBot/1.0 (+{settings.BASE_URL})',
                'Accept': 'text/html',
                'Accept-Encoding': 'identity',
                'Connection': 'close',
            },
        )
        reply = connection.getresponse()
        headers = {name.lower(): value for name, value in reply.getheaders()}
        body = b''
        if reply.status == 200:
            while len(body) < MAX_BODY_BYTES:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError('too slow')
                chunk = reply.read1(min(16 * 1024, MAX_BODY_BYTES - len(body)))
                if not chunk:
                    break
                body += chunk
        if time.monotonic() > deadline:
            # The watchdog cut the connection, which reads as the end of the body.
            raise TimeoutError('too slow')
        return Response(reply.status, headers, body)
    finally:
        watchdog.cancel()
        connection.close()


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.done = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == 'title' and not self.done:
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == 'title' and self.in_title:
            self.in_title = False
            self.done = True

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.parts.append(data)


def _charset(content_type: str) -> str:
    for piece in content_type.split(';')[1:]:
        name, _, value = piece.partition('=')
        if name.strip().lower() == 'charset':
            return value.strip().strip('"\'') or 'utf-8'
    return 'utf-8'


def extract_title(body: bytes, content_type: str = 'text/html') -> str:
    """The text of the first `<title>` in `body`, whitespace collapsed and cut to 255 characters;
    empty when there is none."""
    try:
        text = body.decode(_charset(content_type), errors='replace')
    except LookupError:
        text = body.decode('utf-8', errors='replace')
    parser = _TitleParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # the stdlib parser is lenient, but a hostile page must never break us
        pass
    title = ' '.join(''.join(parser.parts).split())
    return title[:MAX_TITLE_LENGTH]


def fetch_page_title(url: str) -> str:
    """The title of the page at `url`, or an empty string when it can't be fetched safely."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            current = validate_destination(current)
            parts = urlsplit(current)
            host = parts.hostname or ''
            port = parts.port or (443 if parts.scheme == 'https' else 80)
            ip = resolve_public(host, port)
            reply = http_get(current, ip, deadline)
        except ValidationError, UnsafeDestination, OSError, TimeoutError, http.client.HTTPException:
            return ''
        if reply.status in (301, 302, 303, 307, 308):
            location = reply.headers.get('location', '')
            if not location:
                return ''
            current = urljoin(current, location)
            continue
        content_type = reply.headers.get('content-type', '')
        encoding = reply.headers.get('content-encoding', 'identity').lower()
        if (
            reply.status != 200
            or content_type.split(';')[0].strip().lower() != 'text/html'
            or encoding not in ('', 'identity')
        ):
            return ''
        return extract_title(reply.body, content_type)
    return ''


@task
def fetch_title(link_id: str) -> None:
    """Fill in the title of a link that has none from its destination page. Never replaces a
    title: one the owner typed in the meantime wins."""
    link = ShortLink.objects.filter(pk=link_id).only('id', 'destination', 'title').first()
    if link is None or link.title:
        return
    try:
        title = fetch_page_title(link.destination)
    except Exception:
        logger.warning('Title fetch failed for short link %s', link_id, exc_info=True)
        return
    if title:
        ShortLink.objects.filter(pk=link_id, title='').update(title=title)
