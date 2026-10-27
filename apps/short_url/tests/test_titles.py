import http.client
import http.server
import socket
import threading
import time

import pytest

from ..models import ShortLink
from ..services import titles
from ..services.titles import (
    MAX_BODY_BYTES,
    MAX_REDIRECTS,
    Response,
    UnsafeDestination,
    extract_title,
    fetch_page_title,
    fetch_title,
    resolve_public,
)
from ..services.titles import http_get as real_http_get  # bound before `no_network` patches it
from .factories import create_link

PUBLIC_IP = '93.184.216.34'


def html(body: str = '<html><head><title>Hello</title></head></html>', **headers) -> Response:
    headers.setdefault('content-type', 'text/html; charset=utf-8')
    return Response(200, headers, body.encode())


def redirect(location: str, status: int = 302) -> Response:
    return Response(status, {'location': location}, b'')


@pytest.fixture
def web(monkeypatch):
    """Replace the resolver and the HTTP call. `pages` maps a URL to a `Response` (or an
    exception to raise); `resolved` maps a host to the address(es) it resolves to (default:
    public). Everything that was fetched is recorded in `fetched` as `(url, ip)`."""

    class Web:
        def __init__(self):
            self.pages: dict[str, Response | Exception] = {}
            self.resolved: dict[str, str | Exception] = {}
            self.fetched: list[tuple[str, str]] = []

    state = Web()

    def resolve(host, port):
        answer = state.resolved.get(host, PUBLIC_IP)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(url, ip, deadline):
        state.fetched.append((url, ip))
        page = state.pages[url]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(titles, 'resolve_public', resolve)
    monkeypatch.setattr(titles, 'http_get', get)
    return state


class TestExtractTitle:
    def test_the_first_title(self):
        assert extract_title(b'<title>One</title><title>Two</title>') == 'One'

    def test_whitespace_is_collapsed(self):
        assert extract_title(b'<title>\n  A   lot\t of\n space  </title>') == 'A lot of space'

    def test_entities_are_decoded(self):
        assert extract_title(b'<title>Tom &amp; Jerry &#8211; &quot;Hi&quot;</title>') == (
            'Tom & Jerry – "Hi"'
        )

    def test_none_is_an_empty_string(self):
        assert extract_title(b'<html><body>no title</body></html>') == ''
        assert extract_title(b'') == ''

    def test_a_title_that_never_closes_is_still_read(self):
        assert extract_title(b'<title>Cut off') == 'Cut off'

    def test_it_is_cut_to_255_characters(self):
        assert len(extract_title(('<title>' + 'x' * 1000 + '</title>').encode())) == 255

    def test_the_charset_of_the_header_is_used(self):
        body = '<title>Café</title>'.encode('latin-1')
        assert extract_title(body, 'text/html; charset=ISO-8859-1') == 'Café'

    def test_an_unknown_charset_falls_back_to_utf8(self):
        assert extract_title('<title>Café</title>'.encode(), 'text/html; charset=nope') == ('Café')

    def test_broken_bytes_do_not_raise(self):
        assert extract_title(b'<title>ok \xff\xfe</title>').startswith('ok')

    def test_a_hostile_page_does_not_raise(self):
        assert isinstance(extract_title(b'<' * 10_000 + b'<title>x'), str)


class TestResolvePublic:
    @staticmethod
    def answer(monkeypatch, *addresses):
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (a, 80)) for a in addresses]
        monkeypatch.setattr(titles.socket, 'getaddrinfo', lambda *args, **kwargs: infos)

    def test_a_public_address_is_returned(self, monkeypatch):
        self.answer(monkeypatch, PUBLIC_IP)
        assert resolve_public('example.com', 80) == PUBLIC_IP

    @pytest.mark.parametrize(
        'address',
        [
            '127.0.0.1',
            '10.1.2.3',
            '172.20.0.1',
            '192.168.0.9',
            '169.254.169.254',
            '100.64.0.1',
            '0.0.0.0',
            '224.0.0.1',
            '::1',
            'fe80::1',
            'fc00::1',
            '::ffff:127.0.0.1',
            '::ffff:10.0.0.1',
        ],
    )
    def test_a_non_public_address_is_refused(self, monkeypatch, address):
        self.answer(monkeypatch, address)
        with pytest.raises(UnsafeDestination):
            resolve_public('example.com', 80)

    def test_one_bad_address_among_good_ones_refuses_the_host(self, monkeypatch):
        self.answer(monkeypatch, PUBLIC_IP, '10.0.0.1')
        with pytest.raises(UnsafeDestination):
            resolve_public('rebind.example', 80)

    def test_a_scoped_ipv6_address_is_read(self, monkeypatch):
        infos = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('fe80::1%en0', 80, 0, 0))]
        monkeypatch.setattr(titles.socket, 'getaddrinfo', lambda *args, **kwargs: infos)
        with pytest.raises(UnsafeDestination):
            resolve_public('example.com', 80)

    def test_a_name_that_does_not_resolve_is_refused(self, monkeypatch):
        def fail(*args, **kwargs):
            raise socket.gaierror('nope')

        monkeypatch.setattr(titles.socket, 'getaddrinfo', fail)
        with pytest.raises(UnsafeDestination):
            resolve_public('nonexistent.example', 80)

    def test_no_addresses_is_refused(self, monkeypatch):
        monkeypatch.setattr(titles.socket, 'getaddrinfo', lambda *args, **kwargs: [])
        with pytest.raises(UnsafeDestination):
            resolve_public('example.com', 80)


class TestFetchPageTitle:
    def test_the_title_of_a_page(self, web):
        web.pages['https://example.com/'] = html()
        assert fetch_page_title('https://example.com/') == 'Hello'
        assert web.fetched == [('https://example.com/', PUBLIC_IP)]

    def test_it_connects_to_the_address_that_was_checked(self, web):
        web.resolved['example.com'] = '203.0.113.9'
        web.pages['https://example.com/'] = html()
        fetch_page_title('https://example.com/')
        assert web.fetched[0][1] == '203.0.113.9'

    def test_a_private_host_is_never_requested(self, web):
        web.resolved['intranet.example.com'] = UnsafeDestination('private')
        assert fetch_page_title('https://intranet.example.com/') == ''
        assert web.fetched == []

    @pytest.mark.parametrize(
        'url',
        [
            'http://127.0.0.1/',
            'http://169.254.169.254/latest/meta-data/',
            'http://localhost/',
            'ftp://example.com/',
            'file:///etc/passwd',
            'http://[::1]/',
        ],
    )
    def test_an_unacceptable_address_is_refused_before_anything_happens(self, web, url):
        assert fetch_page_title(url) == ''
        assert web.fetched == []

    def test_redirects_are_followed_and_checked_again(self, web):
        web.pages['https://example.com/a'] = redirect('/b')
        web.pages['https://example.com/b'] = redirect('https://other.example.org/c')
        web.pages['https://other.example.org/c'] = html('<title>Landed</title>')
        web.resolved['other.example.org'] = '198.51.100.7'
        assert fetch_page_title('https://example.com/a') == 'Landed'
        assert web.fetched == [
            ('https://example.com/a', PUBLIC_IP),
            ('https://example.com/b', PUBLIC_IP),
            ('https://other.example.org/c', '198.51.100.7'),
        ]

    @pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
    def test_every_redirect_status_is_followed(self, web, status):
        web.pages['https://example.com/a'] = redirect('https://example.com/b', status)
        web.pages['https://example.com/b'] = html()
        assert fetch_page_title('https://example.com/a') == 'Hello'

    def test_a_redirect_to_a_private_address_is_refused(self, web):
        web.pages['https://example.com/a'] = redirect('http://10.0.0.1/admin')
        assert fetch_page_title('https://example.com/a') == ''
        assert web.fetched == [('https://example.com/a', PUBLIC_IP)]

    def test_a_redirect_to_a_host_that_resolves_privately_is_refused(self, web):
        web.pages['https://example.com/a'] = redirect('https://rebind.example.net/')
        web.resolved['rebind.example.net'] = UnsafeDestination('private')
        assert fetch_page_title('https://example.com/a') == ''
        assert len(web.fetched) == 1

    @pytest.mark.parametrize('target', ['ftp://example.com/x', 'file:///etc/passwd', 'gopher://x'])
    def test_a_redirect_to_another_scheme_is_refused(self, web, target):
        web.pages['https://example.com/a'] = redirect(target)
        assert fetch_page_title('https://example.com/a') == ''

    def test_a_redirect_without_a_location_gives_up(self, web):
        web.pages['https://example.com/a'] = Response(302, {}, b'')
        assert fetch_page_title('https://example.com/a') == ''

    def test_at_most_three_redirects(self, web):
        assert MAX_REDIRECTS == 3
        for i in range(6):
            web.pages[f'https://example.com/{i}'] = redirect(f'/{i + 1}')
        web.pages['https://example.com/6'] = html()
        assert fetch_page_title('https://example.com/0') == ''
        assert len(web.fetched) == 4  # the first request and three redirects

    def test_exactly_three_redirects_is_fine(self, web):
        for i in range(3):
            web.pages[f'https://example.com/{i}'] = redirect(f'/{i + 1}')
        web.pages['https://example.com/3'] = html()
        assert fetch_page_title('https://example.com/0') == 'Hello'

    def test_a_redirect_loop_ends(self, web):
        web.pages['https://example.com/a'] = redirect('/a')
        assert fetch_page_title('https://example.com/a') == ''

    @pytest.mark.parametrize(
        'content_type',
        [
            'application/pdf',
            'application/json',
            'text/plain',
            'image/png',
            'application/xhtml+xml',
            '',
        ],
    )
    def test_only_html_is_read(self, web, content_type):
        web.pages['https://example.com/'] = html(**{'content-type': content_type})
        assert fetch_page_title('https://example.com/') == ''

    def test_the_content_type_may_carry_parameters_and_any_case(self, web):
        web.pages['https://example.com/'] = html(**{'content-type': 'Text/HTML; charset=UTF-8'})
        assert fetch_page_title('https://example.com/') == 'Hello'

    @pytest.mark.parametrize('status', [204, 400, 401, 403, 404, 410, 429, 500, 503])
    def test_an_error_status_gives_no_title(self, web, status):
        web.pages['https://example.com/'] = Response(
            status, {'content-type': 'text/html'}, b'<title>Error page</title>'
        )
        assert fetch_page_title('https://example.com/') == ''

    @pytest.mark.parametrize('encoding', ['gzip', 'br', 'deflate'])
    def test_a_compressed_answer_is_not_read(self, web, encoding):
        web.pages['https://example.com/'] = html(**{'content-encoding': encoding})
        assert fetch_page_title('https://example.com/') == ''

    def test_an_identity_encoding_is_fine(self, web):
        web.pages['https://example.com/'] = html(**{'content-encoding': 'identity'})
        assert fetch_page_title('https://example.com/') == 'Hello'

    @pytest.mark.parametrize(
        'error',
        [
            TimeoutError('slow'),
            ConnectionRefusedError(),
            OSError('network is unreachable'),
            http.client.BadStatusLine('x'),
        ],
    )
    def test_network_errors_leave_the_title_empty(self, web, error):
        web.pages['https://example.com/'] = error
        assert fetch_page_title('https://example.com/') == ''

    def test_the_destination_is_normalized_before_it_is_requested(self, web):
        web.pages['https://xn--bcher-kva.example/%C3%BC'] = html()
        assert fetch_page_title('https://bücher.example/ü') == 'Hello'


class _Handler(http.server.BaseHTTPRequestHandler):
    seen_host: list[str] = []

    def do_GET(self):  # noqa: N802
        _Handler.seen_host.append(self.headers.get('Host', ''))
        if self.path == '/big':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            try:
                self.wfile.write(b'<title>t</title>' + b'x' * (4 * MAX_BODY_BYTES))
            except OSError:
                pass
        elif self.path == '/slow':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b'x')
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass
        elif self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://elsewhere.example/')
            self.end_headers()
        else:
            body = b'<html><title>Local page</title></html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def local_server():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.seen_host.clear()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


class TestHttpGet:
    """The real connection code, against a server on this machine (no outside network)."""

    def test_it_connects_to_the_given_address_and_sends_the_real_host(self, local_server):
        url = f'http://pinned.example.test:{local_server}/'
        reply = real_http_get(url, '127.0.0.1', time.monotonic() + 5)
        assert reply.status == 200
        assert extract_title(reply.body) == 'Local page'
        assert reply.headers['content-type'].startswith('text/html')
        assert _Handler.seen_host == [f'pinned.example.test:{local_server}']

    def test_the_body_is_cut_at_the_read_cap(self, local_server):
        url = f'http://pinned.example.test:{local_server}/big'
        reply = real_http_get(url, '127.0.0.1', time.monotonic() + 5)
        assert len(reply.body) == MAX_BODY_BYTES
        assert extract_title(reply.body) == 't'

    def test_a_slow_server_is_given_up_on(self, local_server):
        url = f'http://pinned.example.test:{local_server}/slow'
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            real_http_get(url, '127.0.0.1', time.monotonic() + 0.5)
        assert time.monotonic() - started < 3

    def test_a_redirect_is_reported_not_followed(self, local_server):
        url = f'http://pinned.example.test:{local_server}/redirect'
        reply = real_http_get(url, '127.0.0.1', time.monotonic() + 5)
        assert reply.status == 302
        assert reply.headers['location'] == 'http://elsewhere.example/'
        assert reply.body == b''

    def test_a_closed_port_raises(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with pytest.raises(OSError):
            real_http_get(f'http://x.example.test:{port}/', '127.0.0.1', time.monotonic() + 2)


@pytest.mark.django_db
class TestTask:
    def test_it_fills_in_an_empty_title(self, web, user):
        link = create_link(user, destination='https://example.com/page')
        web.pages['https://example.com/page'] = html('<title>Fetched</title>')
        fetch_title.call(str(link.pk))
        assert ShortLink.objects.get(pk=link.pk).title == 'Fetched'

    def test_it_never_replaces_a_title(self, web, user):
        link = create_link(user, destination='https://example.com/page', title='Mine')
        web.pages['https://example.com/page'] = html('<title>Fetched</title>')
        fetch_title.call(str(link.pk))
        assert ShortLink.objects.get(pk=link.pk).title == 'Mine'
        assert web.fetched == []

    def test_a_title_typed_while_it_was_fetching_wins(self, web, user, monkeypatch):
        link = create_link(user, destination='https://example.com/page')

        def slow_fetch(url):
            ShortLink.objects.filter(pk=link.pk).update(title='Typed meanwhile')
            return 'Fetched'

        monkeypatch.setattr(titles, 'fetch_page_title', slow_fetch)
        fetch_title.call(str(link.pk))
        assert ShortLink.objects.get(pk=link.pk).title == 'Typed meanwhile'

    def test_a_failure_leaves_it_empty(self, web, user):
        link = create_link(user, destination='https://example.com/page')
        web.pages['https://example.com/page'] = TimeoutError('slow')
        fetch_title.call(str(link.pk))
        assert ShortLink.objects.get(pk=link.pk).title == ''

    def test_an_unexpected_error_is_swallowed_and_logged(self, user, monkeypatch, caplog):
        link = create_link(user, destination='https://example.com/page')

        def boom(url):
            raise RuntimeError('boom')

        monkeypatch.setattr(titles, 'fetch_page_title', boom)
        fetch_title.call(str(link.pk))
        assert ShortLink.objects.get(pk=link.pk).title == ''
        assert str(link.pk) in caplog.text
        assert 'example.com' not in caplog.text

    def test_a_missing_link_is_a_no_op(self, web, db):
        fetch_title.call('00000000-0000-0000-0000-000000000000')
        assert web.fetched == []

    def test_a_page_without_a_title_leaves_it_empty(self, web, user):
        link = create_link(user, destination='https://example.com/page')
        web.pages['https://example.com/page'] = html('<html></html>')
        fetch_title.call(str(link.pk))
        assert ShortLink.objects.get(pk=link.pk).title == ''
