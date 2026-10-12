"""Unit tests for `admin/receipts.py` (the receipts JWT API CLI, issue #98 phase 4): its pure logic
(checksums, file/folder expansion, URLs) and its HTTP-driven upload and command wiring, with every
network call mocked. Like `test_filetransfer.py`, this module never touches Django or a database --
the CLI is a plain HTTP client over `/api/receipts/`.
"""

import base64
import hashlib
import json

import pytest
import typer
from typer.testing import CliRunner

from admin import receipts

pytestmark = pytest.mark.unit

runner = CliRunner()

RECEIPT: dict = {
    'id': '11111111-1111-1111-1111-111111111111',
    'status': 'processed',
    'failure_reason': '',
    'source': 'api',
    'original_filename': 'staples.jpg',
    'content_type': 'image/jpeg',
    'size_bytes': 1234,
    'sha256': '',
    'vendor': 'Staples',
    'amount': '42.10',
    'currency': 'USD',
    'receipt_date': '2026-10-09',
    'document_type': 'receipt',
    'document_number': 'R-1',
    'payment_method': 'Visa 4242',
    'category': {'id': 3, 'name': 'Office supplies'},
    'notes': 'paper',
    'possible_duplicate_of': None,
    'duplicate_dismissed': False,
    'missing_fields': [],
    'created_at': '2026-10-10T09:00:00Z',
    'updated_at': '2026-10-10T09:00:00Z',
    'processed_at': '2026-10-10T09:00:05Z',
    'deleted_at': None,
}


class FakeResponse:
    """A minimal stand-in for `requests.Response`, enough for `receipts.py`'s own use of it."""

    def __init__(self, status_code=200, json_data=None, headers=None, text=None, content=b''):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400
        self.text = text if text is not None else json.dumps(self._json)
        self._content = content
        self.closed = False
        # Raised from `iter_content` after the first chunk: a connection dropped mid-download.
        self.fail_mid_stream: Exception | None = None

    def close(self):
        self.closed = True

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self.ok:
            import requests

            raise requests.exceptions.HTTPError(f'{self.status_code} error')

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self._content), chunk_size):
            yield self._content[start : start + chunk_size]
            if self.fail_mid_stream:
                raise self.fail_mid_stream


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    """Every command calls `set_environment(environment)` first, which reads a real `.env.*` file
    off disk -- irrelevant to what these tests check, and CI may not have one at all."""
    monkeypatch.setattr(
        receipts, 'set_environment', lambda environment=None, *args, **kwargs: environment
    )


@pytest.fixture(autouse=True)
def _site_url(monkeypatch):
    monkeypatch.delenv('API_BASE_URL', raising=False)
    monkeypatch.delenv('BASE_URL', raising=False)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Neutralize the 429-retry backoff so a rate-limit test doesn't wait in real time."""
    monkeypatch.setattr(receipts.time, 'sleep', lambda *args, **kwargs: None)


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    """The `local` environment's token file (the default), holding a token; the other
    environments' files sit next to it."""
    monkeypatch.setattr(receipts, 'TOKEN_FILE_BASE', tmp_path / 'token')
    monkeypatch.setattr(receipts, '_environment', 'local')
    path = tmp_path / 'token.local'
    path.write_text('tok')
    return path


@pytest.fixture
def logged_out(token_file):
    token_file.unlink()
    return token_file


def _checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


# -- Pure helpers --


def test_api_url_defaults_to_the_local_server():
    assert receipts._api_url('/receipts/inbox') == 'http://localhost:8000/api/receipts/inbox'


def test_api_url_uses_the_environment_files_base_url(monkeypatch):
    # Read at call time, so the value `set_environment` loads into `os.environ` is seen.
    monkeypatch.setenv('BASE_URL', 'https://example.com/')

    assert receipts._api_url('/receipts/inbox') == 'https://example.com/api/receipts/inbox'

    monkeypatch.setenv('API_BASE_URL', 'http://override:9000')
    assert receipts._api_url('/auth/login') == 'http://override:9000/api/auth/login'


def test_sha256_base64_file_matches_hashlib_and_streams(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, 'HASH_CHUNK_BYTES', 4)
    path = tmp_path / 'a.pdf'
    path.write_bytes(b'hello world, in several chunks')

    assert receipts._sha256_base64_file(path) == _checksum(b'hello world, in several chunks')


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('a.pdf', 'application/pdf'),
        ('A.PDF', 'application/pdf'),
        ('a.jpg', 'image/jpeg'),
        ('a.jpeg', 'image/jpeg'),
        ('a.png', 'image/png'),
        ('a.webp', 'image/webp'),
        ('a.heic', 'image/heic'),
        ('a.heif', 'image/heic'),
        ('a.txt', None),
        ('a', None),
    ],
)
def test_content_type_by_extension(tmp_path, name, expected):
    assert receipts._content_type(tmp_path / name) == expected


def test_collect_files_expands_folders_and_skips_unsupported_and_empty(tmp_path, monkeypatch):
    (tmp_path / 'a.pdf').write_bytes(b'a')
    (tmp_path / 'notes.txt').write_text('x')
    (tmp_path / 'empty.jpg').write_bytes(b'')
    sub = tmp_path / 'sub'
    sub.mkdir()
    (sub / 'b.png').write_bytes(b'b')
    warnings = []
    monkeypatch.setattr(receipts.logger, 'warning', lambda msg: warnings.append(msg))

    files = receipts._collect_files([tmp_path])

    assert [p.name for p in files] == ['a.pdf', 'b.png']
    assert any('notes.txt' in w and 'unsupported' in w for w in warnings)
    assert any('empty.jpg' in w for w in warnings)


def test_collect_files_warns_about_an_explicit_unsupported_file(tmp_path, monkeypatch):
    path = tmp_path / 'sheet.xlsx'
    path.write_bytes(b'x')
    warnings = []
    monkeypatch.setattr(receipts.logger, 'warning', lambda msg: warnings.append(msg))

    assert receipts._collect_files([path]) == []
    assert any('sheet.xlsx' in w for w in warnings)


def test_collect_files_missing_path_exits(tmp_path):
    with pytest.raises(typer.Exit):
        receipts._collect_files([tmp_path / 'missing.pdf'])


def test_raise_for_api_error_is_a_noop_for_ok_response():
    receipts._raise_for_api_error(FakeResponse(200))


def test_raise_for_api_error_exits_on_error_response():
    with pytest.raises(typer.Exit):
        receipts._raise_for_api_error(FakeResponse(400, {'detail': 'bad request'}))


def test_raise_for_api_error_handles_non_json_body():
    class NotJson(FakeResponse):
        def json(self):
            raise ValueError('no json')

    with pytest.raises(typer.Exit):
        receipts._raise_for_api_error(NotJson(500, text='<html>gateway error</html>'))


def test_error_text_carries_the_detail_and_a_hint():
    assert receipts._error_text(FakeResponse(400, {'detail': 'bad'})) == '400: bad'
    assert 'login' in receipts._error_text(FakeResponse(401, {'detail': 'Unauthorized'}))
    assert 'credits' in receipts._error_text(FakeResponse(402, {'detail': 'paused'}))


def test_error_text_shows_ninjas_422_list_readably():
    detail = [
        {'type': 'extra_forbidden', 'loc': ['body', 'payload', 'vendr'], 'msg': 'Extra inputs'},
        {'type': 'int_parsing', 'loc': ['query', 'limit'], 'msg': 'Input should be an integer'},
        {'type': 'uuid_parsing', 'loc': ['path', 'receipt_id'], 'msg': 'Input should be a UUID'},
        {'type': 'missing', 'loc': [], 'msg': 'Field required'},
    ]

    text = receipts._error_text(FakeResponse(422, {'detail': detail}))

    assert text == (
        '422: vendr: Extra inputs; limit: Input should be an integer; '
        'receipt_id: Input should be a UUID; Field required'
    )


def test_error_text_copes_with_json_that_is_not_an_object_with_a_detail():
    assert receipts._error_text(FakeResponse(500, ['boom'], text='["boom"]')) == '500: ["boom"]'
    assert receipts._error_text(FakeResponse(500, 'oops', text='"oops"')) == '500: "oops"'
    assert receipts._error_text(FakeResponse(500, {'x': 1}, text='{"x": 1}')) == '500: {"x": 1}'
    assert receipts._error_text(FakeResponse(400, {'detail': {'a': 1}})) == "400: {'a': 1}"


def test_error_text_for_a_429_says_when_to_try_again():
    response = FakeResponse(429, {'detail': 'Too many.'}, headers={'Retry-After': '3500'})

    assert (
        receipts._error_text(response) == '429: Too many. (rate limited, try again in 59 minutes)'
    )
    assert 'try again' not in receipts._error_text(FakeResponse(429, {'detail': 'Too many.'}))


def test_get_headers_requires_login(logged_out):
    with pytest.raises(typer.Exit):
        receipts.get_headers()


def test_get_headers_returns_bearer_token(token_file):
    token_file.write_text('abc123')

    assert receipts.get_headers() == {'Authorization': 'Bearer abc123'}


def test_save_token_creates_file_with_owner_only_permissions(logged_out):
    receipts.save_token('secret')

    assert logged_out.read_text() == 'secret'
    assert (logged_out.stat().st_mode & 0o777) == 0o600


def test_save_token_tightens_a_file_that_already_exists_with_looser_permissions(token_file):
    token_file.chmod(0o644)

    receipts.save_token('secret')

    assert token_file.read_text() == 'secret'
    assert (token_file.stat().st_mode & 0o777) == 0o600


def test_save_token_will_not_write_through_a_symlink(logged_out, tmp_path):
    target = tmp_path / 'elsewhere'
    target.write_text('keep')
    logged_out.symlink_to(target)

    with pytest.raises(OSError):
        receipts.save_token('secret')

    assert target.read_text() == 'keep'


# -- One token file per environment --


def test_each_environment_has_its_own_token_file(token_file, tmp_path, monkeypatch):
    token_file.write_text('local-token')
    (tmp_path / 'token.prod').write_text('prod-token')

    monkeypatch.setattr(receipts, '_environment', 'prod')
    assert receipts.get_headers() == {'Authorization': 'Bearer prod-token'}

    monkeypatch.setattr(receipts, '_environment', 'local')
    assert receipts.get_headers() == {'Authorization': 'Bearer local-token'}


def test_commands_use_the_selected_environments_token(token_file, tmp_path, monkeypatch):
    (tmp_path / 'token.prod').write_text('prod-token')
    seen = {}

    def fake_get(url, params=None, headers=None, **kwargs):
        seen.update(url=url, headers=headers)
        return FakeResponse(200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []})

    monkeypatch.setattr('requests.get', fake_get)

    assert runner.invoke(receipts.app, ['list', 'prod']).exit_code == 0
    assert seen['headers'] == {'Authorization': 'Bearer prod-token'}
    assert runner.invoke(receipts.app, ['list', 'local']).exit_code == 0
    assert seen['headers'] == {'Authorization': 'Bearer tok'}


def test_login_writes_only_the_selected_environments_file(token_file, tmp_path, monkeypatch):
    monkeypatch.setattr(
        'requests.post', lambda *a, **k: FakeResponse(200, {'access_token': 'new-prod'})
    )

    result = runner.invoke(receipts.app, ['login', 'prod', 'me@example.com', 'pw'])

    assert result.exit_code == 0
    assert (tmp_path / 'token.prod').read_text() == 'new-prod'
    assert token_file.read_text() == 'tok'


def test_a_token_from_an_older_version_is_ignored(logged_out, tmp_path):
    (tmp_path / 'token').write_text('legacy')

    with pytest.raises(typer.Exit):
        receipts.get_headers()


# -- `_request`: timeout + bounded 429 retry --


def test_request_passes_a_default_timeout(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(200)

    monkeypatch.setattr('requests.get', fake_get)

    receipts._request('get', 'https://example.com')

    assert seen['timeout'] == receipts.REQUEST_TIMEOUT_SECONDS


def test_request_retries_a_429_honouring_retry_after(monkeypatch):
    calls = []
    slept = []
    monkeypatch.setattr(receipts.time, 'sleep', lambda s: slept.append(s))

    def fake_post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return FakeResponse(429, headers={'Retry-After': '3'})
        return FakeResponse(200, {'ok': True})

    monkeypatch.setattr('requests.post', fake_post)

    response = receipts._request('post', 'https://example.com')

    assert response.status_code == 200
    assert len(calls) == 2
    assert slept == [3.0]


def test_request_gives_up_after_max_retries(monkeypatch):
    calls = []

    def always_429(url, **kwargs):
        calls.append(1)
        return FakeResponse(429)

    monkeypatch.setattr('requests.get', always_429)

    response = receipts._request('get', 'https://example.com')

    assert response.status_code == 429
    assert len(calls) == receipts.MAX_RATE_LIMIT_RETRIES + 1


def test_request_does_not_sleep_on_a_long_retry_after(monkeypatch):
    # The hourly rules (export, regenerate, ...): waiting would hang the command for an hour.
    slept = []
    calls = []
    monkeypatch.setattr(receipts.time, 'sleep', lambda s: slept.append(s))

    def hourly(url, **kwargs):
        calls.append(1)
        return FakeResponse(429, {'detail': 'Too many requests.'}, headers={'Retry-After': '3000'})

    monkeypatch.setattr('requests.get', hourly)

    response = receipts._request('get', 'https://example.com')

    assert response.status_code == 429
    assert slept == [] and len(calls) == 1


def test_request_sleeps_at_the_cap_but_not_past_it(monkeypatch):
    slept = []
    monkeypatch.setattr(receipts.time, 'sleep', lambda s: slept.append(s))
    waits = iter(['60', '61'])

    def get(url, **kwargs):
        return FakeResponse(429, headers={'Retry-After': next(waits)})

    monkeypatch.setattr('requests.get', get)

    assert receipts._request('get', 'https://example.com').status_code == 429
    assert slept == [60.0]  # 60 s is fine, the next 61 s is not


def test_request_stops_retrying_once_the_total_wait_budget_is_spent(monkeypatch):
    slept = []
    calls = []
    monkeypatch.setattr(receipts.time, 'sleep', lambda s: slept.append(s))

    def always_55(url, **kwargs):
        calls.append(1)
        return FakeResponse(429, headers={'Retry-After': '55'})

    monkeypatch.setattr('requests.get', always_55)

    assert receipts._request('get', 'https://example.com').status_code == 429
    assert sum(slept) <= receipts.MAX_TOTAL_RETRY_WAIT_SECONDS
    assert slept == [55.0, 55.0]  # a third 55 s would pass the 120 s budget
    assert len(calls) == 3


def test_request_closes_the_429_responses_it_discards(monkeypatch):
    first = FakeResponse(429, headers={'Retry-After': '1'})
    final = FakeResponse(200)
    responses = iter([first, final])
    monkeypatch.setattr('requests.get', lambda url, **kwargs: next(responses))

    assert receipts._request('get', 'https://example.com', stream=True) is final
    assert first.closed and not final.closed


def test_a_long_rate_limit_fails_at_once_telling_when_to_try_again(
    token_file, tmp_path, monkeypatch
):
    slept = []
    monkeypatch.setattr(receipts.time, 'sleep', lambda s: slept.append(s))
    monkeypatch.setattr(
        'requests.get',
        lambda *a, **k: FakeResponse(
            429, {'detail': 'Too many requests.'}, headers={'Retry-After': '2400'}
        ),
    )
    out = tmp_path / 'out.csv'

    result = runner.invoke(receipts.app, ['export', 'local', '--out', str(out)])

    assert result.exit_code == 1
    assert slept == [] and not out.exists()
    assert 'rate limited, try again in 40 minutes' in result.output


@pytest.mark.parametrize(
    ('seconds', 'text'),
    [
        (1, '1 second'),
        (45, '45 seconds'),
        (60, '1 minute'),
        (61, '2 minutes'),
        (3600, '60 minutes'),
    ],
)
def test_format_wait(seconds, text):
    assert receipts._format_wait(seconds) == text


def test_a_network_error_exits_1_with_a_message_in_every_command(token_file, tmp_path, monkeypatch):
    import requests

    def refuse(*args, **kwargs):
        raise requests.exceptions.ConnectionError('connection refused')

    for verb in ('get', 'post', 'patch'):
        monkeypatch.setattr(f'requests.{verb}', refuse)
    for args in (
        ['list', 'local'],
        ['get', 'local', 'abc'],
        ['inbox', 'local'],
        ['regenerate', 'local', '--yes'],
        ['export', 'local', '--out', str(tmp_path / 'o.csv')],
        ['login', 'local', 'me@example.com', 'pw'],
    ):
        result = runner.invoke(receipts.app, args)
        assert result.exit_code == 1, args
        assert 'connection refused' in result.output, args
        assert not isinstance(result.exception, requests.exceptions.RequestException), args


def test_request_raises_network_error_when_asked_to(monkeypatch):
    import requests

    def refuse(*args, **kwargs):
        raise requests.exceptions.Timeout('timed out')

    monkeypatch.setattr('requests.put', refuse)

    with pytest.raises(receipts.NetworkError, match='timed out'):
        receipts._request('put', 'https://example.com', exit_on_error=False)


# -- The upload (`_upload_receipt`), with every HTTP call mocked --


class FakeApi:
    """Records the calls of one upload and answers like the API and S3 would."""

    def __init__(self, monkeypatch, *, start=None, put=None, complete=None):
        self.calls: list[tuple[str, str, dict]] = []
        self._start = start or FakeResponse(
            201,
            {
                'receipt_id': 'r1',
                'url': 'https://s3.example.com/put?sig',
                'headers': {'x-amz-checksum-sha256': 'CHECKSUM'},
                'expires_in': 3600,
            },
        )
        self._put = put or FakeResponse(200)
        self._complete = complete or FakeResponse(
            200, {**RECEIPT, 'id': 'r1', 'status': 'received'}
        )
        monkeypatch.setattr('requests.post', self.post)
        monkeypatch.setattr('requests.put', self.put)

    def post(self, url, **kwargs):
        self.calls.append(('post', url, kwargs))
        return self._complete if url.endswith('/complete') else self._start

    def put(self, url, **kwargs):
        self.calls.append(('put', url, kwargs))
        return self._put


def test_upload_receipt_happy_path(tmp_path, monkeypatch):
    path = tmp_path / 'staples.jpg'
    path.write_bytes(b'jpeg-bytes')
    api = FakeApi(monkeypatch)

    receipt = receipts._upload_receipt({'Authorization': 'Bearer tok'}, path)

    assert receipt['id'] == 'r1'
    (_, start_url, start), (_, put_url, put), (_, complete_url, complete) = api.calls
    assert start_url.endswith('/api/receipts/uploads')
    assert start['json'] == {
        'filename': 'staples.jpg',
        'size': 10,
        'content_type': 'image/jpeg',
        'checksum_sha256': _checksum(b'jpeg-bytes'),
    }
    assert start['headers'] == {'Authorization': 'Bearer tok'}
    # The bytes go to the presigned URL with exactly the headers the API returned, and without
    # our bearer token (it is a different host).
    assert put_url == 'https://s3.example.com/put?sig'
    assert put['data'] == b'jpeg-bytes'
    assert put['headers'] == {'x-amz-checksum-sha256': 'CHECKSUM'}
    assert put['timeout'] == receipts.FILE_UPLOAD_TIMEOUT_SECONDS
    assert complete_url.endswith('/api/receipts/uploads/r1/complete')
    assert complete['headers'] == {'Authorization': 'Bearer tok'}


def test_upload_receipt_reports_the_apis_refusal_and_does_not_put(tmp_path, monkeypatch):
    path = tmp_path / 'a.pdf'
    path.write_bytes(b'x')
    api = FakeApi(monkeypatch, start=FakeResponse(402, {'detail': 'Your receipts are paused'}))

    with pytest.raises(receipts.UploadFailed, match='402: Your receipts are paused'):
        receipts._upload_receipt({}, path)

    assert [method for method, _, _ in api.calls] == ['post']


def test_upload_receipt_reports_a_failed_put_and_does_not_complete(tmp_path, monkeypatch):
    path = tmp_path / 'a.pdf'
    path.write_bytes(b'x')
    api = FakeApi(monkeypatch, put=FakeResponse(403))

    with pytest.raises(receipts.UploadFailed, match='storage failed'):
        receipts._upload_receipt({}, path)

    assert [method for method, _, _ in api.calls] == ['post', 'put']


def test_upload_receipt_reports_a_network_error_on_the_put(tmp_path, monkeypatch):
    import requests

    path = tmp_path / 'a.pdf'
    path.write_bytes(b'x')
    FakeApi(monkeypatch)

    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError('reset')

    monkeypatch.setattr('requests.put', boom)

    with pytest.raises(receipts.UploadFailed, match='reset'):
        receipts._upload_receipt({}, path)


def test_upload_receipt_reports_a_rejection_at_complete(tmp_path, monkeypatch):
    path = tmp_path / 'a.pdf'
    path.write_bytes(b'x')
    FakeApi(monkeypatch, complete=FakeResponse(400, {'detail': 'The uploaded file is corrupted.'}))

    with pytest.raises(receipts.UploadFailed, match='corrupted'):
        receipts._upload_receipt({}, path)


# -- Command wiring (typer CliRunner) --


def test_login_success_saves_token(logged_out, monkeypatch):
    seen = {}

    def fake_post(url, json=None, **kwargs):
        seen['url'] = url
        return FakeResponse(200, {'access_token': 'tok123'})

    monkeypatch.setattr('requests.post', fake_post)

    result = runner.invoke(receipts.app, ['login', 'local', 'me@example.com', 'pw'])

    assert result.exit_code == 0
    assert logged_out.read_text().strip() == 'tok123'
    assert seen['url'] == 'http://localhost:8000/api/auth/login'


def test_login_prompts_for_password_when_omitted(logged_out, monkeypatch):
    seen = {}

    def fake_post(url, json: dict | None = None, **kwargs):
        assert json is not None
        seen['password'] = json['password']
        return FakeResponse(200, {'access_token': 'tok123'})

    monkeypatch.setattr('requests.post', fake_post)
    monkeypatch.setattr(receipts.typer, 'prompt', lambda *a, **k: 'typed-pw')

    result = runner.invoke(receipts.app, ['login', 'local', 'me@example.com'])

    assert result.exit_code == 0
    assert seen['password'] == 'typed-pw'


def test_login_reads_password_from_env_var(logged_out, monkeypatch):
    seen = {}

    def fake_post(url, json: dict | None = None, **kwargs):
        assert json is not None
        seen['password'] = json['password']
        return FakeResponse(200, {'access_token': 'tok123'})

    monkeypatch.setattr('requests.post', fake_post)
    monkeypatch.setenv('RECEIPTS_PASSWORD', 'env-pw')

    result = runner.invoke(receipts.app, ['login', 'local', 'me@example.com'])

    assert result.exit_code == 0
    assert seen['password'] == 'env-pw'


def test_login_failure_exits_nonzero(logged_out, monkeypatch):
    monkeypatch.setattr('requests.post', lambda *a, **k: FakeResponse(401, {'detail': 'no'}))

    result = runner.invoke(receipts.app, ['login', 'local', 'me@example.com', 'pw'])

    assert result.exit_code == 1
    assert not logged_out.exists()


def test_login_failure_shows_the_apis_own_message(logged_out, monkeypatch):
    monkeypatch.setattr(
        'requests.post',
        lambda *a, **k: FakeResponse(403, {'detail': 'Please confirm your email address.'}),
    )

    result = runner.invoke(receipts.app, ['login', 'local', 'me@example.com', 'pw'])

    assert result.exit_code == 1
    assert 'Please confirm your email address.' in result.output
    assert 'login` again' not in result.output  # no "run login again" hint at the login itself


def test_login_that_cannot_save_the_token_exits_nonzero(logged_out, monkeypatch):
    monkeypatch.setattr(
        'requests.post', lambda *a, **k: FakeResponse(200, {'access_token': 'tok123'})
    )
    logged_out.symlink_to(logged_out.parent / 'elsewhere')

    result = runner.invoke(receipts.app, ['login', 'local', 'me@example.com', 'pw'])

    assert result.exit_code == 1
    assert 'Could not save the token' in result.output


def test_commands_without_login_exit_nonzero(logged_out):
    for args in (
        ['list', 'local'],
        ['get', 'local', 'abc'],
        ['inbox', 'local'],
        ['regenerate', 'local', '--yes'],
        ['export', 'local', '--out', 'x.csv'],
    ):
        assert runner.invoke(receipts.app, args).exit_code == 1


def test_list_with_no_receipts(token_file, monkeypatch):
    monkeypatch.setattr(
        'requests.get',
        lambda *a, **k: FakeResponse(200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []}),
    )

    result = runner.invoke(receipts.app, ['list', 'local'])

    assert result.exit_code == 0
    assert 'No receipts found' in result.output


def test_list_passes_the_filters_through(token_file, monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, **kwargs):
        seen.update(url=url, params=params, headers=headers)
        return FakeResponse(200, {'count': 0, 'limit': 5, 'offset': 10, 'results': []})

    monkeypatch.setattr('requests.get', fake_get)

    result = runner.invoke(
        receipts.app,
        [
            'list',
            'local',
            '--status',
            'needs_review',
            '--category',
            '3',
            '--q',
            'staples',
            '--from',
            '2026-10-01',
            '--to',
            '2026-10-31',
            '--sort',
            'created_at',
            '--duplicates',
            '--limit',
            '5',
            '--offset',
            '10',
        ],
    )

    assert result.exit_code == 0
    assert seen['url'] == 'http://localhost:8000/api/receipts/receipts'
    assert seen['headers'] == {'Authorization': 'Bearer tok'}
    assert seen['params'] == {
        'sort': 'created_at',
        'limit': 5,
        'offset': 10,
        'status': 'needs_review',
        'category': '3',
        'q': 'staples',
        'date_from': '2026-10-01',
        'date_to': '2026-10-31',
        'duplicates': 'true',
    }


def test_list_defaults_send_no_filters(token_file, monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen['params'] = params
        return FakeResponse(200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []})

    monkeypatch.setattr('requests.get', fake_get)

    runner.invoke(receipts.app, ['list', 'local'])

    assert seen['params'] == {'sort': '-receipt_date', 'limit': 20, 'offset': 0}


def test_list_shows_receipts_and_hints_at_more_pages(token_file, monkeypatch):
    unnamed = {**RECEIPT, 'id': '22222222-2222-2222-2222-222222222222', 'vendor': ''}
    unnamed.update(amount=None, receipt_date=None, category=None, status='needs_review')
    monkeypatch.setattr(
        'requests.get',
        lambda *a, **k: FakeResponse(
            200, {'count': 3, 'limit': 2, 'offset': 0, 'results': [RECEIPT, unnamed]}
        ),
    )

    result = runner.invoke(receipts.app, ['list', 'local'], env={'COLUMNS': '200'})

    assert result.exit_code == 0
    assert RECEIPT['id'] in result.output  # the full id: `get` needs all of it
    assert 'Staples' in result.output and '42.10 USD' in result.output
    assert 'Office supplies' in result.output
    assert 'staples.jpg' in result.output  # no vendor yet: the file name stands in
    assert '--offset 2' in result.output


def test_list_api_error_exits_nonzero(token_file, monkeypatch):
    monkeypatch.setattr(
        'requests.get', lambda *a, **k: FakeResponse(400, {'detail': 'Unknown status: x.'})
    )

    result = runner.invoke(receipts.app, ['list', 'local', '--status', 'x'])

    assert result.exit_code == 1


def test_upload_uploads_every_supported_file(token_file, tmp_path, monkeypatch):
    (tmp_path / 'a.jpg').write_bytes(b'aaa')
    (tmp_path / 'b.pdf').write_bytes(b'bbb')
    (tmp_path / 'c.txt').write_text('skip me')
    uploaded = []
    monkeypatch.setattr(
        receipts,
        '_upload_receipt',
        lambda headers, path: (
            uploaded.append(path.name) or {'id': f'id-{path.name}', 'status': 'x'}
        ),
    )

    result = runner.invoke(receipts.app, ['upload', 'local', str(tmp_path)])

    assert result.exit_code == 0
    assert uploaded == ['a.jpg', 'b.pdf']
    assert 'id-a.jpg' in result.output and 'id-b.pdf' in result.output
    assert '2 of 2 uploaded' in result.output


def test_upload_carries_on_after_a_failure_and_exits_nonzero(token_file, tmp_path, monkeypatch):
    (tmp_path / 'a.jpg').write_bytes(b'aaa')
    (tmp_path / 'b.pdf').write_bytes(b'bbb')

    def fake_upload(headers, path):
        if path.name == 'a.jpg':
            raise receipts.UploadFailed('400: This file type is not supported.')
        return {'id': 'id-b', 'status': 'received'}

    monkeypatch.setattr(receipts, '_upload_receipt', fake_upload)

    result = runner.invoke(
        receipts.app, ['upload', 'local', str(tmp_path / 'a.jpg'), str(tmp_path / 'b.pdf')]
    )

    assert result.exit_code == 1
    assert 'a.jpg: 400: This file type is not supported.' in result.output
    assert 'id-b' in result.output
    assert '1 of 2 uploaded' in result.output


def test_upload_carries_on_after_a_network_error(token_file, tmp_path, monkeypatch):
    (tmp_path / 'a.jpg').write_bytes(b'aaa')
    (tmp_path / 'b.pdf').write_bytes(b'bbb')

    def fake_upload(headers, path):
        if path.name == 'a.jpg':
            raise receipts.NetworkError('connection refused')
        return {'id': 'id-b', 'status': 'received'}

    monkeypatch.setattr(receipts, '_upload_receipt', fake_upload)

    result = runner.invoke(
        receipts.app, ['upload', 'local', str(tmp_path / 'a.jpg'), str(tmp_path / 'b.pdf')]
    )

    assert result.exit_code == 1
    assert 'a.jpg: connection refused' in result.output
    assert '1 of 2 uploaded' in result.output


def test_upload_with_nothing_supported_exits_nonzero(token_file, tmp_path):
    (tmp_path / 'notes.txt').write_text('x')

    result = runner.invoke(receipts.app, ['upload', 'local', str(tmp_path / 'notes.txt')])

    assert result.exit_code == 1
    assert 'No files to upload' in result.output


def test_upload_end_to_end_with_mocked_http(token_file, tmp_path, monkeypatch):
    path = tmp_path / 'scan.png'
    path.write_bytes(b'png-bytes')
    api = FakeApi(monkeypatch)

    result = runner.invoke(receipts.app, ['upload', 'local', str(path)])

    assert result.exit_code == 0
    assert [method for method, _, _ in api.calls] == ['post', 'put', 'post']
    assert 'scan.png: uploaded as r1 (received)' in result.output


def test_get_prints_the_receipt(token_file, monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen['url'] = url
        return FakeResponse(200, RECEIPT)

    monkeypatch.setattr('requests.get', fake_get)

    result = runner.invoke(receipts.app, ['get', 'local', RECEIPT['id']])

    assert result.exit_code == 0
    assert seen['url'] == f'http://localhost:8000/api/receipts/receipts/{RECEIPT["id"]}'
    for text in ('Staples', '42.10 USD', '2026-10-09', 'Office supplies', 'paper', 'processed'):
        assert text in result.output


def test_get_not_found_exits_nonzero(token_file, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(404, {'detail': 'Not Found'}))

    result = runner.invoke(receipts.app, ['get', 'local', 'nope'])

    assert result.exit_code == 1
    assert '404' in result.output


def test_export_streams_the_csv_to_the_file(token_file, tmp_path, monkeypatch):
    seen = {}
    csv_bytes = b'receipt_date,vendor\r\n2026-10-09,Staples\r\n'

    def fake_get(url, params=None, headers=None, **kwargs):
        seen.update(url=url, params=params, stream=kwargs.get('stream'))
        return FakeResponse(200, content=csv_bytes)

    monkeypatch.setattr('requests.get', fake_get)
    out = tmp_path / 'out.csv'

    result = runner.invoke(
        receipts.app,
        ['export', 'local', '--out', str(out), '--from', '2026-10-01', '--status', 'processed'],
    )

    assert result.exit_code == 0
    assert out.read_bytes() == csv_bytes
    assert seen['url'] == 'http://localhost:8000/api/receipts/export.csv'
    assert seen['stream'] is True
    assert seen['params'] == {
        'sort': '-receipt_date',
        'status': 'processed',
        'date_from': '2026-10-01',
    }


def test_export_overwrites_an_existing_file_only_on_success(token_file, tmp_path, monkeypatch):
    out = tmp_path / 'out.csv'
    out.write_bytes(b'old,good\r\n')
    import requests

    response = FakeResponse(200, content=b'a,b\r\n1,2\r\n3,4\r\n')
    response.fail_mid_stream = requests.exceptions.ChunkedEncodingError('reset by peer')
    monkeypatch.setattr('requests.get', lambda *a, **k: response)

    result = runner.invoke(receipts.app, ['export', 'local', '--out', str(out)])

    assert result.exit_code == 1
    assert 'reset by peer' in result.output
    assert out.read_bytes() == b'old,good\r\n'  # the earlier file is untouched
    assert sorted(p.name for p in tmp_path.iterdir()) == ['out.csv', 'token.local']
    assert response.closed


def test_export_mid_stream_failure_leaves_no_partial_file(token_file, tmp_path, monkeypatch):
    import requests

    response = FakeResponse(200, content=b'a,b\r\n1,2\r\n')
    response.fail_mid_stream = requests.exceptions.ChunkedEncodingError('cut off')
    monkeypatch.setattr('requests.get', lambda *a, **k: response)
    out = tmp_path / 'out.csv'

    result = runner.invoke(receipts.app, ['export', 'local', '--out', str(out)])

    assert result.exit_code == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ['token.local']


def test_export_leaves_no_temp_file_behind_on_success(token_file, tmp_path, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, content=b'a,b\r\n'))
    out = tmp_path / 'out.csv'

    result = runner.invoke(receipts.app, ['export', 'local', '--out', str(out)])

    assert result.exit_code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ['out.csv', 'token.local']


def test_export_into_a_missing_folder_fails_before_asking_the_server(
    token_file, tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr('requests.get', lambda *a, **k: calls.append(1) or FakeResponse(200))

    result = runner.invoke(
        receipts.app, ['export', 'local', '--out', str(tmp_path / 'nope' / 'out.csv')]
    )

    assert result.exit_code == 1 and calls == []


def test_export_error_writes_no_file(token_file, tmp_path, monkeypatch):
    monkeypatch.setattr(
        'requests.get', lambda *a, **k: FakeResponse(429, {'detail': 'Too many requests.'})
    )
    out = tmp_path / 'out.csv'

    result = runner.invoke(receipts.app, ['export', 'local', '--out', str(out)])

    assert result.exit_code == 1
    assert not out.exists()


def test_export_requires_out(token_file):
    assert runner.invoke(receipts.app, ['export', 'local']).exit_code != 0


INBOX = {
    'address': 'receipts+abcd2345@ziptrigo.com',
    'sender_allowlist': [],
    'default_currency': 'USD',
    'forwarding_confirmation': None,
}


def test_inbox_shows_the_address(token_file, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, INBOX))

    result = runner.invoke(receipts.app, ['inbox', 'local'])

    assert result.exit_code == 0
    assert 'receipts+abcd2345@ziptrigo.com' in result.output
    assert 'anyone' in result.output and 'USD' in result.output


def test_inbox_shows_the_allowlist_and_a_forwarding_confirmation(token_file, monkeypatch):
    inbox = {
        **INBOX,
        'sender_allowlist': ['me@example.com', '*@shop.example'],
        'forwarding_confirmation': {
            'received_at': '2026-10-10T09:00:00Z',
            'code': '123456789',
            'url': 'https://mail-settings.google.com/mail/vf-abc',
        },
    }
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, inbox))

    result = runner.invoke(receipts.app, ['inbox', 'local'])

    assert result.exit_code == 0
    assert 'me@example.com, *@shop.example' in result.output
    assert '123456789' in result.output
    assert 'https://mail-settings.google.com/mail/vf-abc' in result.output


def test_regenerate_asks_first_and_aborts_on_no(token_file, monkeypatch):
    calls = []
    monkeypatch.setattr(
        'requests.post', lambda *a, **k: calls.append(a) or FakeResponse(200, INBOX)
    )

    result = runner.invoke(receipts.app, ['regenerate', 'local'], input='n\n')

    assert result.exit_code == 1
    assert calls == []
    assert 'Cancelled' in result.output


def test_regenerate_proceeds_on_yes(token_file, monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen['url'] = url
        return FakeResponse(200, {**INBOX, 'address': 'receipts+newcode22@ziptrigo.com'})

    monkeypatch.setattr('requests.post', fake_post)

    result = runner.invoke(receipts.app, ['regenerate', 'local'], input='y\n')

    assert result.exit_code == 0
    assert seen['url'] == 'http://localhost:8000/api/receipts/inbox/regenerate'
    assert 'receipts+newcode22@ziptrigo.com' in result.output


def test_regenerate_yes_skips_the_prompt(token_file, monkeypatch):
    monkeypatch.setattr('requests.post', lambda *a, **k: FakeResponse(200, INBOX))

    result = runner.invoke(receipts.app, ['regenerate', 'local', '--yes'])

    assert result.exit_code == 0
    assert 'Generate a new one' not in result.output


def test_regenerate_rate_limited_exits_nonzero(token_file, monkeypatch):
    monkeypatch.setattr(
        'requests.post',
        lambda *a, **k: FakeResponse(429, {'detail': 'Too many requests.'}, headers={}),
    )

    result = runner.invoke(receipts.app, ['regenerate', 'local', '--yes'])

    assert result.exit_code == 1
