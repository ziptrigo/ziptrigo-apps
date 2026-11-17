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
    'vendor_id': 12,
    'vendor_vetted': True,
    'vendor_extracted': 'Staples Inc.',
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
        self.fail_mid_stream: BaseException | None = None

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
def _no_sleep(monkeypatch):
    """Neutralize the 429-retry backoff so a rate-limit test doesn't wait in real time."""
    monkeypatch.setattr(receipts.time, 'sleep', lambda *args, **kwargs: None)


@pytest.fixture
def token_file(token_dir):
    """The `local` environment's token file (the default), holding a token; the other
    environments' files sit next to it."""
    path = token_dir / '.receipts_token.local'
    path.write_text('tok')
    return path


@pytest.fixture
def logged_out(token_file):
    token_file.unlink()
    return token_file


def _checksum(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


# -- Pure helpers --


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


# -- One token file per environment (the helpers themselves: test_cli_utils.py) --


def test_commands_talk_to_the_selected_environments_site(token_file, loads_base_url, monkeypatch):
    (token_file.parent / '.receipts_token.prod').write_text('prod-token')
    seen = {}

    def fake_get(url, params=None, headers=None, **kwargs):
        seen.update(url=url)
        return FakeResponse(200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []})

    monkeypatch.setattr('requests.get', fake_get)

    assert runner.invoke(receipts.app, ['list', 'prod']).exit_code == 0
    assert seen['url'] == 'https://prod.example/api/receipts/receipts'


def test_commands_use_the_selected_environments_token(token_file, tmp_path, monkeypatch):
    (tmp_path / '.receipts_token.prod').write_text('prod-token')
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
    assert (tmp_path / '.receipts_token.prod').read_text() == 'new-prod'
    assert token_file.read_text() == 'tok'


def test_a_token_from_an_older_version_is_ignored(logged_out, tmp_path):
    (tmp_path / '.receipts_token').write_text('legacy')

    result = runner.invoke(receipts.app, ['list', 'local'])

    assert result.exit_code == 1


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
        ['vendors', 'local'],
        ['vendor-rename', 'local', '1', 'ACME'],
        ['vendor-vet', 'local', '1'],
        ['vendor-merge', 'local', '1', '2', '--yes'],
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
            '--vendor',
            '12',
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
        'vendor': 12,
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


def test_get_shows_the_vendors_state_and_the_name_it_was_read_as(token_file, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, RECEIPT))

    result = runner.invoke(receipts.app, ['get', 'local', RECEIPT['id']])

    assert 'Staples (vetted)' in result.output
    assert 'Vendor ID: 12' in result.output
    assert 'Read as: Staples Inc.' in result.output


def test_get_says_when_the_vendor_is_not_vetted(token_file, monkeypatch):
    receipt = {**RECEIPT, 'vendor_vetted': False}
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, receipt))

    result = runner.invoke(receipts.app, ['get', 'local', RECEIPT['id']])

    assert 'Staples (not vetted)' in result.output


def test_get_leaves_out_read_as_when_it_is_the_vendors_name(token_file, monkeypatch):
    receipt = {**RECEIPT, 'vendor_extracted': ' staples '}
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, receipt))

    result = runner.invoke(receipts.app, ['get', 'local', RECEIPT['id']])

    assert 'Read as' not in result.output


def test_get_copes_with_a_receipt_that_has_no_vendor(token_file, monkeypatch):
    receipt = {
        **RECEIPT,
        'vendor': '',
        'vendor_id': None,
        'vendor_vetted': False,
        'vendor_extracted': '',
    }
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, receipt))

    result = runner.invoke(receipts.app, ['get', 'local', RECEIPT['id']])

    assert result.exit_code == 0
    assert 'Vendor' not in result.output and 'Read as' not in result.output


def test_get_copes_with_an_older_server_without_the_vendor_fields(token_file, monkeypatch):
    receipt = {k: v for k, v in RECEIPT.items() if not k.startswith('vendor_')}
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, receipt))

    result = runner.invoke(receipts.app, ['get', 'local', RECEIPT['id']])

    assert result.exit_code == 0 and 'Staples' in result.output


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
        [
            'export',
            'local',
            '--out',
            str(out),
            '--from',
            '2026-10-01',
            '--status',
            'processed',
            '--vendor',
            '12',
        ],
    )

    assert result.exit_code == 0
    assert out.read_bytes() == csv_bytes
    assert seen['url'] == 'http://localhost:8000/api/receipts/export.csv'
    assert seen['stream'] is True
    assert seen['params'] == {
        'sort': '-receipt_date',
        'status': 'processed',
        'vendor': 12,
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
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.receipts_token.local', 'out.csv']
    assert response.closed


def test_export_mid_stream_failure_leaves_no_partial_file(token_file, tmp_path, monkeypatch):
    import requests

    response = FakeResponse(200, content=b'a,b\r\n1,2\r\n')
    response.fail_mid_stream = requests.exceptions.ChunkedEncodingError('cut off')
    monkeypatch.setattr('requests.get', lambda *a, **k: response)
    out = tmp_path / 'out.csv'

    result = runner.invoke(receipts.app, ['export', 'local', '--out', str(out)])

    assert result.exit_code == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.receipts_token.local']


def test_export_leaves_no_temp_file_behind_on_success(token_file, tmp_path, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, content=b'a,b\r\n'))
    out = tmp_path / 'out.csv'

    result = runner.invoke(receipts.app, ['export', 'local', '--out', str(out)])

    assert result.exit_code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.receipts_token.local', 'out.csv']


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


# -- Vendors --

VENDOR: dict = {
    'id': 12,
    'name': 'ACME',
    'vetted': True,
    'vetted_at': '2026-10-10T09:00:00Z',
    'aliases': ['ACME Industries, LLC'],
    'receipt_count': 3,
    'trashed_count': 0,
    'last_receipt_date': '2026-10-09',
}
TO_VET: dict = {
    **VENDOR,
    'id': 13,
    'name': 'Staples',
    'vetted': False,
    'vetted_at': None,
    'receipt_count': 1,
    'trashed_count': 2,
    'last_receipt_date': None,
}


def vendor_page(*vendors, count=None, offset=0):
    return {
        'count': len(vendors) if count is None else count,
        'limit': 20,
        'offset': offset,
        'results': list(vendors),
    }


def test_vendors_lists_them(token_file, monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, **kwargs):
        seen.update(url=url, params=params, headers=headers)
        return FakeResponse(200, vendor_page(VENDOR, TO_VET))

    monkeypatch.setattr('requests.get', fake_get)

    result = runner.invoke(receipts.app, ['vendors', 'local'], env={'COLUMNS': '200'})

    assert result.exit_code == 0
    assert seen['url'] == 'http://localhost:8000/api/receipts/vendors'
    assert seen['headers'] == {'Authorization': 'Bearer tok'}
    assert seen['params'] == {'status': 'all', 'limit': 20, 'offset': 0}
    for text in ('ACME', 'Staples', '12', '13', 'yes', 'to vet', '2026-10-09'):
        assert text in result.output
    assert '(+2 in trash)' in result.output


def test_vendors_passes_the_filters_through(token_file, monkeypatch):
    seen = {}

    def fake_get(url, params=None, **kwargs):
        seen['params'] = params
        return FakeResponse(200, vendor_page())

    monkeypatch.setattr('requests.get', fake_get)

    result = runner.invoke(
        receipts.app,
        ['vendors', 'local', '--to-vet', '--q', 'acme', '--limit', '5', '--offset', '10'],
    )

    assert result.exit_code == 0
    assert seen['params'] == {'status': 'to_vet', 'limit': 5, 'offset': 10, 'q': 'acme'}


def test_vendors_with_none(token_file, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(200, vendor_page()))

    result = runner.invoke(receipts.app, ['vendors', 'local'])

    assert result.exit_code == 0 and 'No vendors found' in result.output


def test_vendors_hints_at_more_pages(token_file, monkeypatch):
    monkeypatch.setattr(
        'requests.get', lambda *a, **k: FakeResponse(200, vendor_page(VENDOR, count=5))
    )

    result = runner.invoke(receipts.app, ['vendors', 'local'])

    assert '--offset 1' in result.output


def test_vendors_api_error_exits_nonzero(token_file, monkeypatch):
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(401, {'detail': 'nope'}))

    result = runner.invoke(receipts.app, ['vendors', 'local'])

    assert result.exit_code == 1 and 'login' in result.output


def test_vendor_commands_without_login_exit_nonzero(logged_out):
    for args in (
        ['vendors', 'local'],
        ['vendor-rename', 'local', '1', 'x'],
        ['vendor-vet', 'local', '1'],
        ['vendor-merge', 'local', '1', '2', '--yes'],
    ):
        assert runner.invoke(receipts.app, args).exit_code == 1, args


def test_vendor_rename(token_file, monkeypatch):
    seen = {}

    def fake_patch(url, json=None, headers=None, **kwargs):
        seen.update(url=url, json=json, headers=headers)
        return FakeResponse(200, VENDOR)

    monkeypatch.setattr('requests.patch', fake_patch)

    result = runner.invoke(receipts.app, ['vendor-rename', 'local', '12', 'ACME'])

    assert result.exit_code == 0
    assert seen['url'] == 'http://localhost:8000/api/receipts/vendors/12'
    assert seen['json'] == {'name': 'ACME'}  # renaming vets by default: nothing extra sent
    assert seen['headers'] == {'Authorization': 'Bearer tok'}
    assert 'Renamed' in result.output and 'ACME' in result.output and '3 receipts' in result.output


def test_vendor_rename_not_vetted(token_file, monkeypatch):
    seen = {}

    def fake_patch(url, json=None, **kwargs):
        seen['json'] = json
        return FakeResponse(200, {**VENDOR, 'vetted': False})

    monkeypatch.setattr('requests.patch', fake_patch)

    result = runner.invoke(
        receipts.app, ['vendor-rename', 'local', '12', 'ACME, LTD', '--not-vetted']
    )

    assert result.exit_code == 0
    assert seen['json'] == {'name': 'ACME, LTD', 'vetted': False}
    assert 'not vetted' in result.output


def test_vendor_rename_clash_names_the_other_vendor_and_suggests_a_merge(token_file, monkeypatch):
    body = {
        'detail': 'You already have a vendor called ACME.',
        'conflicting_vendor_id': 7,
        'conflicting_vendor_name': 'ACME',
    }
    monkeypatch.setattr('requests.patch', lambda *a, **k: FakeResponse(409, body))

    result = runner.invoke(receipts.app, ['vendor-rename', 'local', '12', 'ACME'])

    assert result.exit_code == 1
    assert 'Not renamed' in result.output and "'ACME' (id 7)" in result.output
    assert 'receipts vendor-merge local 7 12' in result.output
    assert 'receipts vendor-rename local 7 ACME' in result.output  # the follow-up for the spelling


def test_vendor_rename_clash_hint_quotes_a_name_with_spaces(token_file, monkeypatch):
    body = {
        'detail': 'You already have a vendor called ACME.',
        'conflicting_vendor_id': 7,
        'conflicting_vendor_name': 'ACME',
    }
    monkeypatch.setattr('requests.patch', lambda *a, **k: FakeResponse(409, body))

    result = runner.invoke(receipts.app, ['vendor-rename', 'local', '12', 'ACME, LTD'])

    assert result.exit_code == 1
    assert "'ACME' (id 7)" in result.output
    output = ' '.join(result.output.split())  # the console wraps long lines
    assert "receipts vendor-rename local 7 'ACME, LTD'" in output


def test_vendor_rename_clash_without_a_name_in_the_body(token_file, monkeypatch):
    body = {'detail': 'You already have a vendor called Acme.', 'conflicting_vendor_id': 7}
    monkeypatch.setattr('requests.patch', lambda *a, **k: FakeResponse(409, body))

    result = runner.invoke(receipts.app, ['vendor-rename', 'local', '12', 'Acme'])

    assert result.exit_code == 1
    assert "'Acme' (id 7)" in result.output  # quoted like the other case: the requested name
    assert 'vendor-merge local 7 12' in result.output
    assert 'vendor-rename local 7 Acme' in result.output


def test_vendor_rename_other_errors_show_the_apis_message(token_file, monkeypatch):
    for status, message in ((404, 'Not Found'), (400, 'Enter a vendor name.')):
        monkeypatch.setattr(
            'requests.patch',
            lambda *a, status=status, message=message, **k: FakeResponse(
                status, {'detail': message}
            ),
        )

        result = runner.invoke(receipts.app, ['vendor-rename', 'local', '12', 'x'])

        assert result.exit_code == 1
        assert f'{status}: {message}' in result.output
        assert 'vendor-merge' not in result.output


def test_a_409_that_is_not_a_clash_is_an_ordinary_error(token_file, monkeypatch):
    monkeypatch.setattr('requests.patch', lambda *a, **k: FakeResponse(409, {'detail': 'busy'}))

    result = runner.invoke(receipts.app, ['vendor-rename', 'local', '12', 'x'])

    assert result.exit_code == 1 and '409: busy' in result.output
    assert 'vendor-merge' not in result.output


def test_vendor_vet_several(token_file, monkeypatch):
    calls = []

    def fake_patch(url, json=None, **kwargs):
        calls.append((url, json))
        return FakeResponse(200, VENDOR)

    monkeypatch.setattr('requests.patch', fake_patch)

    result = runner.invoke(receipts.app, ['vendor-vet', 'local', '12', '13', '12'])

    assert result.exit_code == 0
    assert calls == [
        ('http://localhost:8000/api/receipts/vendors/12', {'vetted': True}),
        ('http://localhost:8000/api/receipts/vendors/13', {'vetted': True}),
    ]  # a repeated id is sent once
    assert 'Vetted' in result.output


def test_vendor_vet_unvet(token_file, monkeypatch):
    seen = {}

    def fake_patch(url, json=None, **kwargs):
        seen['json'] = json
        return FakeResponse(200, {**VENDOR, 'vetted': False})

    monkeypatch.setattr('requests.patch', fake_patch)

    result = runner.invoke(receipts.app, ['vendor-vet', 'local', '12', '--unvet'])

    assert result.exit_code == 0
    assert seen['json'] == {'vetted': False} and 'Not vetted' in result.output


def test_vendor_vet_carries_on_after_a_failure_and_exits_nonzero(token_file, monkeypatch):
    def fake_patch(url, json=None, **kwargs):
        if url.endswith('/12'):
            return FakeResponse(404, {'detail': 'Not Found'})
        return FakeResponse(200, {**VENDOR, 'id': 13, 'name': 'Staples'})

    monkeypatch.setattr('requests.patch', fake_patch)

    result = runner.invoke(receipts.app, ['vendor-vet', 'local', '12', '13'])

    assert result.exit_code == 1
    assert 'Vendor 12: 404: Not Found' in result.output
    assert 'Staples' in result.output  # the second one was still done


def test_vendor_merge_asks_first_and_aborts_on_no(token_file, monkeypatch):
    posts = []
    by_id = {12: VENDOR, 13: TO_VET}
    monkeypatch.setattr(
        'requests.get', lambda url, **k: FakeResponse(200, by_id[int(url.rsplit('/', 1)[1])])
    )
    monkeypatch.setattr(
        'requests.post', lambda *a, **k: posts.append(a) or FakeResponse(200, VENDOR)
    )

    result = runner.invoke(receipts.app, ['vendor-merge', 'local', '12', '13'], input='n\n')

    assert result.exit_code == 1
    assert posts == []
    assert 'Cancelled' in result.output
    # The prompt says which vendor is kept and which is merged away.
    assert 'Keep:' in result.output and 'ACME' in result.output
    assert 'Merge:' in result.output and 'Staples' in result.output


def test_vendor_merge_proceeds_on_yes(token_file, monkeypatch):
    seen = {}
    by_id = {12: VENDOR, 13: TO_VET, 14: {**TO_VET, 'id': 14, 'name': 'Staples Inc'}}
    monkeypatch.setattr(
        'requests.get', lambda url, **k: FakeResponse(200, by_id[int(url.rsplit('/', 1)[1])])
    )

    def fake_post(url, json=None, headers=None, **kwargs):
        seen.update(url=url, json=json, headers=headers)
        return FakeResponse(200, {**VENDOR, 'receipt_count': 6})

    monkeypatch.setattr('requests.post', fake_post)

    result = runner.invoke(receipts.app, ['vendor-merge', 'local', '12', '13', '14'], input='y\n')

    assert result.exit_code == 0
    assert seen['url'] == 'http://localhost:8000/api/receipts/vendors/merge'
    assert seen['json'] == {'keep': 12, 'merge': [13, 14]}
    assert seen['headers'] == {'Authorization': 'Bearer tok'}
    assert 'Merged' in result.output and '6 receipts' in result.output


def test_vendor_merge_yes_skips_the_prompt_and_the_lookups(token_file, monkeypatch):
    seen = {}

    def no_get(*args, **kwargs):
        raise AssertionError('--yes should not look the vendors up first')

    monkeypatch.setattr('requests.get', no_get)

    def fake_post(url, json=None, **kwargs):
        seen['json'] = json
        return FakeResponse(200, VENDOR)

    monkeypatch.setattr('requests.post', fake_post)

    result = runner.invoke(receipts.app, ['vendor-merge', 'local', '12', '13', '--yes'])

    assert result.exit_code == 0
    assert seen['json'] == {'keep': 12, 'merge': [13]}
    assert 'Merge?' not in result.output


def test_vendor_merge_ignores_the_kept_id_and_repeats_in_the_list(token_file, monkeypatch):
    seen = {}

    def fake_post(url, json=None, **kwargs):
        seen['json'] = json
        return FakeResponse(200, VENDOR)

    monkeypatch.setattr('requests.post', fake_post)

    result = runner.invoke(receipts.app, ['vendor-merge', 'local', '12', '13', '12', '13', '-y'])

    assert result.exit_code == 0 and seen['json'] == {'keep': 12, 'merge': [13]}


def test_vendor_merge_with_nothing_to_merge_exits_without_a_request(token_file, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('no request expected')

    monkeypatch.setattr('requests.post', fail)
    monkeypatch.setattr('requests.get', fail)

    result = runner.invoke(receipts.app, ['vendor-merge', 'local', '12', '12', '--yes'])

    assert result.exit_code == 1 and 'besides the one to keep' in result.output


def test_vendor_merge_stops_before_asking_when_a_vendor_is_unknown(token_file, monkeypatch):
    posts = []
    monkeypatch.setattr('requests.get', lambda *a, **k: FakeResponse(404, {'detail': 'Not Found'}))
    monkeypatch.setattr('requests.post', lambda *a, **k: posts.append(a) or FakeResponse(200))

    result = runner.invoke(receipts.app, ['vendor-merge', 'local', '12', '99'])

    assert result.exit_code == 1 and '404' in result.output
    assert posts == [] and 'Merge?' not in result.output


def test_vendor_merge_api_error_exits_nonzero(token_file, monkeypatch):
    monkeypatch.setattr('requests.post', lambda *a, **k: FakeResponse(404, {'detail': 'Not Found'}))

    result = runner.invoke(receipts.app, ['vendor-merge', 'local', '12', '13', '--yes'])

    assert result.exit_code == 1 and '404' in result.output


# -- Exports: all the files as zip files (issue #315) -----------------------------------------

EXPORT_ID = '22222222-2222-2222-2222-222222222222'
API = 'http://localhost:8000/api/receipts'
STORAGE_URL = 'https://storage.test/part-1.zip?signed'
ZIP_BYTES = b'PK-the-first-zip-file'


def make_export(status='ready', parts=1, built=True, **fields) -> dict:
    export = {
        'id': EXPORT_ID,
        'status': status,
        'layout': 'year_vendor',
        'filters': {'summary': [], 'params': {}},
        'file_count': 3,
        'skipped_count': 0,
        'total_bytes': 70,
        'part_count': parts,
        'price_per_file': 3,
        'charged_credits': 9,
        'refunded_credits': 0,
        'parts': [
            {
                'number': n,
                'filename': f'receipts-2026-11-17-part-{n}.zip',
                'size_bytes': len(ZIP_BYTES),
                'file_count': 3,
                'built': built,
            }
            for n in range(1, parts + 1)
        ],
        'created_at': '2026-11-17T09:00:00Z',
        'ready_at': '2026-11-17T09:00:30Z' if status == 'ready' else None,
        'expires_at': '2026-12-17T09:00:30Z' if status == 'ready' else None,
    }
    export.update(fields)
    return export


def make_quote(**fields) -> dict:
    quote = {
        'file_count': 3,
        'total_bytes': 70,
        'part_count': 1,
        'price_per_file': 3,
        'cost': 9,
        'balance': 100,
        'affordable': True,
        'frozen': False,
        'retention_days': 30,
        'part_bytes': 100 * 1024 * 1024,
    }
    quote.update(fields)
    return quote


class FakeSite:
    """The export endpoints and the storage URL, as `requests.<method>` replacements. A route is
    a response, a list of responses (served in order, the last one repeated) or a function of the
    call's keyword arguments."""

    def __init__(self, monkeypatch, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []
        for method in ('get', 'post', 'delete'):
            monkeypatch.setattr(f'requests.{method}', self._handler(method))

    def _handler(self, method):
        def handle(url, **kwargs):
            key = (method, url.removeprefix(API) if url.startswith(API) else url)
            self.calls.append((method, key[1], kwargs))
            route = self.routes[key]
            if callable(route):
                return route(**kwargs)
            if isinstance(route, list):
                return route.pop(0) if len(route) > 1 else route[0]
            return route

        return handle

    def made(self, method, path) -> list[dict]:
        return [kw for m, p, kw in self.calls if (m, p) == (method, path)]


def ok(body, **kwargs):
    return FakeResponse(200, body, **kwargs)


def site_for_a_ready_export(monkeypatch, overrides: dict | None = None) -> FakeSite:
    routes = {
        ('post', '/exports/quote'): ok(make_quote()),
        ('post', '/exports'): FakeResponse(201, make_export('building', built=False)),
        ('get', f'/exports/{EXPORT_ID}'): [
            ok(make_export('building', built=False)),
            ok(make_export()),
        ],
        ('get', f'/exports/{EXPORT_ID}/parts/1/url'): ok(
            {
                'url': STORAGE_URL,
                'filename': 'receipts-2026-11-17-part-1.zip',
                'size_bytes': len(ZIP_BYTES),
                'expires_in': 900,
            }
        ),
        ('get', STORAGE_URL): lambda **kw: FakeResponse(200, content=ZIP_BYTES),
    }
    routes.update(overrides or {})
    return FakeSite(monkeypatch, routes)


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch):
    """Rich wraps a long log line (a path, a message) at the terminal's width; keep it on one."""
    monkeypatch.setenv('COLUMNS', '400')


class TestExportFiles:
    def test_quotes_asks_creates_waits_and_downloads(self, token_file, tmp_path, monkeypatch):
        site = site_for_a_ready_export(monkeypatch)
        out = tmp_path / 'zips'

        result = runner.invoke(
            receipts.app,
            [
                'export-files',
                'local',
                '--out',
                str(out),
                '--layout',
                'vendor-year',
                '--status',
                'processed',
                '--vendor',
                '12',
                '--q',
                'acme',
                '--from',
                '2026-01-01',
                '--to',
                '2026-12-31',
                '--duplicates',
            ],
            input='y\n',
        )

        assert result.exit_code == 0, result.output
        selection = {
            'layout': 'vendor_year',
            'status': 'processed',
            'vendor': 12,
            'q': 'acme',
            'date_from': '2026-01-01',
            'date_to': '2026-12-31',
            'duplicates': True,
        }
        assert site.made('post', '/exports/quote')[0]['json'] == selection
        assert site.made('post', '/exports')[0]['json'] == {**selection, 'expected_cost': 9}
        assert 'This costs 9 credits. Continue?' in result.output
        assert (out / 'receipts-2026-11-17-part-1.zip').read_bytes() == ZIP_BYTES
        assert sorted(p.name for p in out.iterdir()) == ['receipts-2026-11-17-part-1.zip']
        assert 'Downloaded 1 zip file' in result.output

    def test_the_api_token_goes_to_the_site_and_never_to_storage(
        self, token_file, tmp_path, monkeypatch
    ):
        site = site_for_a_ready_export(monkeypatch)

        runner.invoke(receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes'])

        for kwargs in site.made('post', '/exports') + site.made('get', f'/exports/{EXPORT_ID}'):
            assert kwargs['headers'] == {'Authorization': 'Bearer tok'}
        (storage_call,) = site.made('get', STORAGE_URL)
        assert 'headers' not in storage_call
        assert storage_call['stream'] is True

    def test_yes_skips_the_question_and_sends_no_expected_cost(
        self, token_file, tmp_path, monkeypatch
    ):
        site = site_for_a_ready_export(monkeypatch)

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 0, result.output
        assert 'Continue?' not in result.output
        assert 'expected_cost' not in site.made('post', '/exports')[0]['json']

    def test_declining_creates_nothing(self, token_file, tmp_path, monkeypatch):
        site = site_for_a_ready_export(monkeypatch)

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path)], input='n\n'
        )

        assert result.exit_code == 1
        assert 'nothing was charged' in result.output
        assert site.made('post', '/exports') == []

    def test_a_free_export_asks_nothing_and_confirms_zero(self, token_file, tmp_path, monkeypatch):
        site = site_for_a_ready_export(
            monkeypatch, {('post', '/exports/quote'): ok(make_quote(cost=0, price_per_file=0))}
        )

        result = runner.invoke(receipts.app, ['export-files', 'local', '--out', str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert 'Continue?' not in result.output
        assert site.made('post', '/exports')[0]['json']['expected_cost'] == 0

    @pytest.mark.parametrize(
        ('quote', 'message'),
        [
            (make_quote(file_count=0, cost=0), 'There are no files to download'),
            (make_quote(frozen=True), 'paused until you add credits'),
            (make_quote(affordable=False, balance=5), 'balance is 5'),
        ],
    )
    def test_a_quote_that_cannot_go_ahead_stops_before_asking(
        self, token_file, tmp_path, monkeypatch, quote, message
    ):
        site = site_for_a_ready_export(monkeypatch, {('post', '/exports/quote'): ok(quote)})

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 1 and message in result.output
        assert site.made('post', '/exports') == []

    def test_a_bad_layout_or_a_file_as_the_folder_fails_before_any_request(
        self, token_file, tmp_path, monkeypatch
    ):
        site = site_for_a_ready_export(monkeypatch)
        a_file = tmp_path / 'file'
        a_file.write_text('x')

        bad_layout = runner.invoke(receipts.app, ['export-files', 'local', '--layout', 'sideways'])
        bad_out = runner.invoke(receipts.app, ['export-files', 'local', '--out', str(a_file)])

        assert bad_layout.exit_code == 1 and 'year-vendor or vendor-year' in bad_layout.output
        assert bad_out.exit_code == 1 and 'Not a folder' in bad_out.output
        assert site.calls == []

    def test_the_layout_accepts_underscores_and_any_case(self, token_file, tmp_path, monkeypatch):
        site = site_for_a_ready_export(monkeypatch)
        runner.invoke(
            receipts.app,
            ['export-files', 'local', '--out', str(tmp_path), '--yes', '--layout', 'Vendor_Year'],
        )
        assert site.made('post', '/exports/quote')[0]['json'] == {'layout': 'vendor_year'}

    @pytest.mark.parametrize(
        ('response', 'message'),
        [
            (
                FakeResponse(409, {'detail': 'An export is already being prepared.'}),
                'An export is already being prepared',
            ),
            (
                FakeResponse(409, {'detail': 'The cost changed: it is now 12 credits.'}),
                'The cost changed since it was quoted',
            ),
            (FakeResponse(402, {'detail': 'Insufficient credits'}), 'top up your credits'),
            (FakeResponse(429, {'detail': 'Slow down'}, headers={'Retry-After': '3000'}), '50 min'),
        ],
    )
    def test_a_refusal_when_creating_is_shown_and_nothing_is_downloaded(
        self, token_file, tmp_path, monkeypatch, response, message
    ):
        site = site_for_a_ready_export(monkeypatch, {('post', '/exports'): response})

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 1 and message in result.output
        assert site.made('get', STORAGE_URL) == []

    def test_a_409_does_not_double_the_full_stop(self, token_file, tmp_path, monkeypatch):
        site_for_a_ready_export(
            monkeypatch, {('post', '/exports'): FakeResponse(409, {'detail': 'Busy.'})}
        )

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert '409: Busy. Check `receipts exports local`, then try again.' in result.output
        assert '..' not in result.output

    def test_a_changed_cost_says_to_run_it_again_in_the_clis_words(
        self, token_file, tmp_path, monkeypatch
    ):
        changed = FakeResponse(
            409,
            {
                'detail': 'The cost changed: it is now 12 credits. Send expected_cost=12 to confirm it.'
            },
        )
        site = site_for_a_ready_export(monkeypatch, {('post', '/exports'): changed})

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 1
        assert 'The cost changed since it was quoted, so nothing was charged.' in result.output
        assert 'Run the command again to see the new quote.' in result.output
        assert 'expected_cost' not in result.output and '..' not in result.output
        assert 'receipts exports' not in result.output
        assert site.made('get', STORAGE_URL) == []

    def test_ctrl_c_while_downloading_says_how_to_fetch_it_later(
        self, token_file, tmp_path, monkeypatch
    ):
        interrupted = FakeResponse(200, content=ZIP_BYTES)
        interrupted.fail_mid_stream = KeyboardInterrupt()
        site_for_a_ready_export(monkeypatch, {('get', STORAGE_URL): interrupted})
        out = tmp_path / 'my zips'

        result = runner.invoke(receipts.app, ['export-files', 'local', '--out', str(out), '--yes'])

        assert result.exit_code == 130
        assert 'Stopped downloading' in result.output and 'paid for' in result.output
        assert f"receipts export-download local {EXPORT_ID} --out '" in result.output
        assert list(out.iterdir()) == []  # no cut-off file left behind

    def test_it_reports_the_progress_while_waiting(self, token_file, tmp_path, monkeypatch):
        building = make_export('building', parts=3, built=False)
        halfway = make_export('building', parts=3, built=False)
        halfway['parts'][1]['built'] = True
        site_for_a_ready_export(
            monkeypatch,
            {
                ('get', f'/exports/{EXPORT_ID}'): [
                    ok(building),
                    ok(halfway),
                    ok(make_export(parts=1)),
                ]
            },
        )

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 0, result.output
        assert 'Written so far: 0 of 3 zip files' in result.output
        assert 'Written so far: 1 of 3 zip files' in result.output

    def test_a_failed_export_is_reported_with_its_refund(self, token_file, tmp_path, monkeypatch):
        failed = make_export('failed', built=False, refunded_credits=9, parts=1)
        failed['parts'] = []
        site = site_for_a_ready_export(monkeypatch, {('get', f'/exports/{EXPORT_ID}'): ok(failed)})

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 1
        assert 'failed and 9 credits were refunded' in result.output
        assert site.made('get', STORAGE_URL) == []

    def test_ctrl_c_stops_waiting_and_says_how_to_fetch_it_later(
        self, token_file, tmp_path, monkeypatch
    ):
        site_for_a_ready_export(
            monkeypatch,
            {('get', f'/exports/{EXPORT_ID}'): ok(make_export('building', built=False))},
        )

        def interrupt(seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(receipts.time, 'sleep', interrupt)

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path / 'my zips'), '--yes']
        )

        assert result.exit_code == 130
        assert 'Stopped waiting' in result.output
        assert f"receipts export-download local {EXPORT_ID} --out '" in result.output

    def test_it_gives_up_waiting_after_the_time_it_was_given(
        self, token_file, tmp_path, monkeypatch
    ):
        site = site_for_a_ready_export(
            monkeypatch,
            {('get', f'/exports/{EXPORT_ID}'): ok(make_export('building', built=False))},
        )

        result = runner.invoke(
            receipts.app,
            ['export-files', 'local', '--out', str(tmp_path), '--yes', '--wait', '0.05'],
        )

        assert result.exit_code == 1
        assert 'Still not ready after 3 seconds' in result.output
        assert f'receipts export-download local {EXPORT_ID}' in result.output
        assert site.made('get', STORAGE_URL) == []

    def test_a_dropped_poll_is_tried_again(self, token_file, tmp_path, monkeypatch):
        import requests

        calls = {'n': 0}

        def poll(**kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise requests.exceptions.ConnectionError('network down')
            return ok(make_export())

        site_for_a_ready_export(monkeypatch, {('get', f'/exports/{EXPORT_ID}'): poll})

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 0, result.output
        assert 'trying again' in result.output

    @pytest.mark.parametrize('status', [502, 503, 429])
    def test_a_transient_answer_while_waiting_is_tried_again(
        self, token_file, tmp_path, monkeypatch, status
    ):
        answers = [
            FakeResponse(status, {'detail': 'busy'}, headers={'Retry-After': '3600'}),
            ok(make_export()),
        ]
        site_for_a_ready_export(monkeypatch, {('get', f'/exports/{EXPORT_ID}'): answers})

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 0, result.output
        assert f'The site answered {status}' in result.output and 'trying again' in result.output

    def test_a_site_that_keeps_failing_ends_with_the_fetch_hint(
        self, token_file, tmp_path, monkeypatch
    ):
        site_for_a_ready_export(
            monkeypatch,
            {('get', f'/exports/{EXPORT_ID}'): FakeResponse(503, {'detail': 'down'})},
        )

        result = runner.invoke(
            receipts.app,
            ['export-files', 'local', '--out', str(tmp_path), '--yes', '--wait', '0.05'],
        )

        assert result.exit_code == 1
        assert f'receipts export-download local {EXPORT_ID}' in result.output

    def test_another_error_while_waiting_prints_the_fetch_hint(
        self, token_file, tmp_path, monkeypatch
    ):
        site_for_a_ready_export(
            monkeypatch,
            {('get', f'/exports/{EXPORT_ID}'): FakeResponse(401, {'detail': 'Invalid token'})},
        )

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 1
        assert f'receipts export-download local {EXPORT_ID}' in result.output

    def test_the_wait_backs_off(self, token_file, tmp_path, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(receipts.time, 'sleep', lambda s: slept.append(s))
        building = ok(make_export('building', built=False))
        site_for_a_ready_export(
            monkeypatch,
            {('get', f'/exports/{EXPORT_ID}'): [building] * 12 + [ok(make_export())]},
        )

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 0, result.output
        assert slept[0] == 3 and slept == sorted(slept) and max(slept) == 15

    def test_a_folder_with_zip_files_is_refused_before_anything_is_quoted_or_charged(
        self, token_file, tmp_path, monkeypatch
    ):
        site = site_for_a_ready_export(monkeypatch)
        existing = tmp_path / 'receipts-2026-11-17-part-1.zip'
        existing.write_bytes(ZIP_BYTES)

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 1
        assert 'Nothing was charged' in result.output and '--force' in result.output
        assert site.made('post', '/exports/quote') == [] and site.made('post', '/exports') == []
        assert existing.read_bytes() == ZIP_BYTES

    def test_a_clash_that_appears_after_creation_says_the_export_is_paid_for(
        self, token_file, tmp_path, monkeypatch
    ):
        calls = {'n': 0}
        existing = tmp_path / 'receipts-2026-11-17-part-1.zip'

        def poll(**kwargs):
            calls['n'] += 1
            existing.write_bytes(b'something else entirely')  # made while waiting
            return ok(make_export())

        site = site_for_a_ready_export(monkeypatch, {('get', f'/exports/{EXPORT_ID}'): poll})

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )

        assert result.exit_code == 1
        assert 'paid for and ready' in result.output
        assert f'export-download local {EXPORT_ID}' in result.output
        assert existing.read_bytes() == b'something else entirely'
        assert site.made('get', STORAGE_URL) == []

    def test_force_replaces_it(self, token_file, tmp_path, monkeypatch):
        site_for_a_ready_export(monkeypatch)
        existing = tmp_path / 'receipts-2026-11-17-part-1.zip'
        existing.write_bytes(b'something else entirely')

        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes', '--force']
        )

        assert result.exit_code == 0, result.output
        assert existing.read_bytes() == ZIP_BYTES

    def test_files_that_vanished_are_mentioned(self, token_file, tmp_path, monkeypatch):
        site_for_a_ready_export(
            monkeypatch,
            {
                ('get', f'/exports/{EXPORT_ID}'): ok(
                    make_export(skipped_count=1, refunded_credits=3)
                )
            },
        )
        result = runner.invoke(
            receipts.app, ['export-files', 'local', '--out', str(tmp_path), '--yes']
        )
        assert result.exit_code == 0
        assert '1 file(s) had vanished' in result.output and '3 credits refunded' in result.output

    def test_it_needs_a_login(self, logged_out):
        result = runner.invoke(receipts.app, ['export-files', 'local'])
        assert result.exit_code != 0


class TestExportDownload:
    def test_a_ready_export_is_downloaded(self, token_file, tmp_path, monkeypatch):
        site = site_for_a_ready_export(
            monkeypatch, {('get', f'/exports/{EXPORT_ID}'): ok(make_export())}
        )

        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path / 'z')]
        )

        assert result.exit_code == 0, result.output
        assert (tmp_path / 'z' / 'receipts-2026-11-17-part-1.zip').read_bytes() == ZIP_BYTES
        assert site.made('post', '/exports') == []  # nothing is created or charged

    def test_ctrl_c_while_downloading_says_how_to_fetch_it_later(
        self, token_file, tmp_path, monkeypatch
    ):
        interrupted = FakeResponse(200, content=ZIP_BYTES)
        interrupted.fail_mid_stream = KeyboardInterrupt()
        site_for_a_ready_export(
            monkeypatch,
            {
                ('get', f'/exports/{EXPORT_ID}'): ok(make_export()),
                ('get', STORAGE_URL): interrupted,
            },
        )

        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path / 'z')]
        )

        assert result.exit_code == 130
        assert 'Stopped downloading' in result.output
        assert f'receipts export-download local {EXPORT_ID} --out' in result.output
        assert list((tmp_path / 'z').iterdir()) == []

    def test_it_waits_for_one_still_being_prepared(self, token_file, tmp_path, monkeypatch):
        site = site_for_a_ready_export(monkeypatch)

        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )

        assert result.exit_code == 0, result.output
        assert 'Preparing your zip files' in result.output
        assert len(site.made('get', f'/exports/{EXPORT_ID}')) == 2  # the first look, one poll
        assert (tmp_path / 'receipts-2026-11-17-part-1.zip').exists()

    def test_a_file_that_is_already_complete_is_skipped(self, token_file, tmp_path, monkeypatch):
        site = site_for_a_ready_export(
            monkeypatch, {('get', f'/exports/{EXPORT_ID}'): ok(make_export())}
        )
        done = tmp_path / 'receipts-2026-11-17-part-1.zip'
        done.write_bytes(ZIP_BYTES)

        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )

        assert result.exit_code == 0, result.output
        assert 'already downloaded, skipped' in result.output
        assert site.made('get', STORAGE_URL) == []

    def test_a_file_of_another_size_is_refused_without_force(
        self, token_file, tmp_path, monkeypatch
    ):
        site = site_for_a_ready_export(
            monkeypatch, {('get', f'/exports/{EXPORT_ID}'): ok(make_export())}
        )
        existing = tmp_path / 'receipts-2026-11-17-part-1.zip'
        existing.write_bytes(b'other')

        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )

        assert result.exit_code == 1 and 'Use --force' in result.output
        assert existing.read_bytes() == b'other' and site.made('get', STORAGE_URL) == []

    def test_force_downloads_again_even_if_complete(self, token_file, tmp_path, monkeypatch):
        site = site_for_a_ready_export(
            monkeypatch, {('get', f'/exports/{EXPORT_ID}'): ok(make_export())}
        )
        (tmp_path / 'receipts-2026-11-17-part-1.zip').write_bytes(ZIP_BYTES)

        result = runner.invoke(
            receipts.app,
            ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path), '--force'],
        )

        assert result.exit_code == 0 and len(site.made('get', STORAGE_URL)) == 1

    def test_an_interrupted_run_is_finished_by_running_it_again(
        self, token_file, tmp_path, monkeypatch
    ):
        export = make_export(parts=2)
        export['parts'][1]['filename'] = 'receipts-2026-11-17-part-2.zip'
        bodies = {1: ZIP_BYTES, 2: ZIP_BYTES}
        urls = {n: f'https://storage.test/part-{n}.zip' for n in (1, 2)}
        routes = {('get', f'/exports/{EXPORT_ID}'): ok(export)}
        for n in (1, 2):
            routes[('get', f'/exports/{EXPORT_ID}/parts/{n}/url')] = ok(
                {
                    'url': urls[n],
                    'filename': f'receipts-2026-11-17-part-{n}.zip',
                    'size_bytes': len(ZIP_BYTES),
                    'expires_in': 900,
                }
            )
        import requests

        broken = FakeResponse(200, content=bodies[2])
        broken.fail_mid_stream = requests.exceptions.ChunkedEncodingError('reset by peer')
        routes[('get', urls[1])] = lambda **kw: FakeResponse(200, content=bodies[1])
        routes[('get', urls[2])] = lambda **kw: broken
        FakeSite(monkeypatch, routes)

        first = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )

        assert first.exit_code == 1
        assert '1 of 2 zip files failed' in first.output and 'reset by peer' in first.output
        assert (tmp_path / 'receipts-2026-11-17-part-1.zip').exists()
        assert not (tmp_path / 'receipts-2026-11-17-part-2.zip').exists()
        assert not list(tmp_path.glob('.receipts-*.part'))  # no cut-off file left behind

        routes[('get', urls[2])] = lambda **kw: FakeResponse(200, content=bodies[2])
        second = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )

        assert second.exit_code == 0, second.output
        assert (tmp_path / 'receipts-2026-11-17-part-2.zip').read_bytes() == ZIP_BYTES

    def test_a_download_of_the_wrong_size_is_not_kept(self, token_file, tmp_path, monkeypatch):
        site_for_a_ready_export(
            monkeypatch,
            {
                ('get', f'/exports/{EXPORT_ID}'): ok(make_export()),
                ('get', STORAGE_URL): lambda **kw: FakeResponse(200, content=b'PK-short'),
            },
        )

        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )

        assert result.exit_code == 1
        assert 'received 8 bytes, expected 21' in result.output
        assert sorted(p.name for p in tmp_path.iterdir()) == ['.receipts_token.local']

    def test_a_storage_error_is_reported(self, token_file, tmp_path, monkeypatch):
        site_for_a_ready_export(
            monkeypatch,
            {
                ('get', f'/exports/{EXPORT_ID}'): ok(make_export()),
                ('get', STORAGE_URL): FakeResponse(403, text='expired'),
            },
        )
        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )
        assert result.exit_code == 1 and 'storage failed (403)' in result.output

    def test_a_refused_url_is_reported_with_the_apis_message(
        self, token_file, tmp_path, monkeypatch
    ):
        site_for_a_ready_export(
            monkeypatch,
            {
                ('get', f'/exports/{EXPORT_ID}'): ok(make_export()),
                ('get', f'/exports/{EXPORT_ID}/parts/1/url'): FakeResponse(
                    402, {'detail': 'Your receipts are paused until you add credits.'}
                ),
            },
        )
        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )
        assert result.exit_code == 1 and 'paused until you add credits' in result.output

    @pytest.mark.parametrize('status', ['failed', 'expired'])
    def test_an_ended_export_has_nothing_to_download(
        self, token_file, tmp_path, monkeypatch, status
    ):
        ended = make_export(status)
        ended['parts'] = []
        site_for_a_ready_export(monkeypatch, {('get', f'/exports/{EXPORT_ID}'): ok(ended)})

        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )

        assert result.exit_code == 1 and f'is {status}' in result.output

    def test_an_unknown_export_is_the_apis_404(self, token_file, tmp_path, monkeypatch):
        site_for_a_ready_export(
            monkeypatch,
            {('get', f'/exports/{EXPORT_ID}'): FakeResponse(404, {'detail': 'Not Found'})},
        )
        result = runner.invoke(
            receipts.app, ['export-download', 'local', EXPORT_ID, '--out', str(tmp_path)]
        )
        assert result.exit_code == 1 and '404' in result.output


class TestExportListGetDelete:
    def test_exports_lists_them(self, token_file, monkeypatch):
        site = FakeSite(
            monkeypatch,
            {
                ('get', '/exports'): ok(
                    {'count': 3, 'limit': 1, 'offset': 0, 'results': [make_export()]}
                )
            },
        )

        result = runner.invoke(receipts.app, ['exports', 'local', '--limit', '1'])

        assert result.exit_code == 0, result.output
        assert site.made('get', '/exports')[0]['params'] == {'limit': 1, 'offset': 0}
        assert EXPORT_ID in result.output and 'ready' in result.output
        assert 'Use --offset 1 to see more' in result.output

    def test_exports_with_none(self, token_file, monkeypatch):
        FakeSite(
            monkeypatch,
            {('get', '/exports'): ok({'count': 0, 'limit': 20, 'offset': 0, 'results': []})},
        )
        result = runner.invoke(receipts.app, ['exports', 'local'])
        assert result.exit_code == 0 and 'No exports' in result.output

    def test_export_get_shows_everything_and_the_parts(self, token_file, monkeypatch):
        export = make_export(skipped_count=1, refunded_credits=3)
        export['filters']['summary'] = ['Vendor: Acme', 'Search: "[red]x[/red]"']
        FakeSite(monkeypatch, {('get', f'/exports/{EXPORT_ID}'): ok(export)})

        result = runner.invoke(receipts.app, ['export-get', 'local', EXPORT_ID])

        assert result.exit_code == 0, result.output
        out = result.output
        assert 'Status: ready' in out and 'Charged: 9 credits' in out
        assert 'Refunded: 3 credits' in out and '1 left out' in out
        assert 'Vendor: Acme; Search: "[red]x[/red]"' in out  # shown as typed, not as markup
        assert 'receipts-2026-11-17-part-1.zip' in out
        assert 'Expires: 2026-12-17T09:00:30Z' in out

    def test_export_get_of_everything(self, token_file, monkeypatch):
        FakeSite(monkeypatch, {('get', f'/exports/{EXPORT_ID}'): ok(make_export())})
        result = runner.invoke(receipts.app, ['export-get', 'local', EXPORT_ID])
        assert 'Selection: everything' in result.output

    def test_export_delete_asks_first(self, token_file, monkeypatch):
        site = FakeSite(monkeypatch, {('delete', f'/exports/{EXPORT_ID}'): FakeResponse(204)})

        declined = runner.invoke(receipts.app, ['export-delete', 'local', EXPORT_ID], input='n\n')
        assert declined.exit_code == 1 and site.made('delete', f'/exports/{EXPORT_ID}') == []

        accepted = runner.invoke(receipts.app, ['export-delete', 'local', EXPORT_ID], input='y\n')
        assert accepted.exit_code == 0 and 'Deleted.' in accepted.output
        assert len(site.made('delete', f'/exports/{EXPORT_ID}')) == 1

    def test_export_delete_yes_skips_the_question(self, token_file, monkeypatch):
        site = FakeSite(monkeypatch, {('delete', f'/exports/{EXPORT_ID}'): FakeResponse(204)})
        result = runner.invoke(receipts.app, ['export-delete', 'local', EXPORT_ID, '--yes'])
        assert result.exit_code == 0 and len(site.calls) == 1

    def test_export_delete_while_building_shows_the_409(self, token_file, monkeypatch):
        FakeSite(
            monkeypatch,
            {
                ('delete', f'/exports/{EXPORT_ID}'): FakeResponse(
                    409, {'detail': 'This export is still being prepared.'}
                )
            },
        )
        result = runner.invoke(receipts.app, ['export-delete', 'local', EXPORT_ID, '--yes'])
        assert result.exit_code == 1 and 'still being prepared' in result.output


def test_size_text():
    assert receipts._size_text(884) == '884 B'
    assert receipts._size_text(1536) == '1.5 KB'
    assert receipts._size_text(100 * 1024 * 1024) == '100.0 MB'
    assert receipts._size_text(3 * 1024**3) == '3.0 GB'
