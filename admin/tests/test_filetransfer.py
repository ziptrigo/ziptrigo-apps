"""Unit tests for `admin/filetransfer.py` (the file_transfer JWT API CLI, issue #55 phase 3):
its pure logic (checksums, file/folder expansion, resume matching) and its HTTP-driven upload and
command wiring, with every network call mocked. This module never touches Django or a database --
it is a plain HTTP client over `/api/ft/` (see its own docstring) -- which is exactly why
`pyproject.toml`'s `testpaths` includes `admin/` alongside `apps/`: `inv test unit` / a bare
`pytest` run then picks this file up the same way it does an app's own tests, without needing
`--ds`/database fixtures it has no use for.
"""

import base64
import hashlib
import json

import pytest
import typer
from typer.testing import CliRunner

from admin import filetransfer

pytestmark = pytest.mark.unit

runner = CliRunner()


class FakeResponse:
    """A minimal stand-in for `requests.Response`, enough for `filetransfer.py`'s own use of it
    (`.ok`, `.status_code`, `.json()`, `.text`, `.headers`, `.raise_for_status()`)."""

    def __init__(self, status_code=200, json_data=None, headers=None, text=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400
        self.text = text if text is not None else json.dumps(self._json)

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self.ok:
            import requests

            raise requests.exceptions.HTTPError(f'{self.status_code} error')


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    """Every command calls `set_environment(environment)` first, which reads a real `.env.*` file
    off disk -- irrelevant to what these tests check (the CLI's own logic and HTTP calls), and this
    worktree/CI may not have one at all. Neutralized once, for every test in this module."""
    monkeypatch.setattr(filetransfer, 'set_environment', lambda *args, **kwargs: None)


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / 'token'
    monkeypatch.setattr(filetransfer, 'TOKEN_FILE', path)
    return path


# -- Pure helpers --


def test_sha256_base64_matches_hashlib():
    data = b'hello world'
    expected = base64.b64encode(hashlib.sha256(data).digest()).decode()
    assert filetransfer._sha256_base64(data) == expected


def test_collect_files_expands_folders_recursively(tmp_path):
    (tmp_path / 'a.txt').write_text('a')
    sub = tmp_path / 'sub'
    sub.mkdir()
    (sub / 'b.txt').write_text('b')

    files = filetransfer._collect_files([tmp_path])

    assert sorted(f.name for f in files) == ['a.txt', 'b.txt']


def test_collect_files_single_file(tmp_path):
    f = tmp_path / 'a.txt'
    f.write_text('a')

    assert filetransfer._collect_files([f]) == [f]


def test_collect_files_missing_path_exits(tmp_path):
    with pytest.raises(typer.Exit):
        filetransfer._collect_files([tmp_path / 'missing.txt'])


def test_find_resumable_matches_by_name_and_size():
    existing = [{'id': '1', 'name': 'a.bin', 'size': 10, 'uploaded': False}]

    assert filetransfer._find_resumable(existing, 'a.bin', 10) == existing[0]
    assert filetransfer._find_resumable(existing, 'a.bin', 20) is None
    assert filetransfer._find_resumable(existing, 'b.bin', 10) is None
    assert filetransfer._find_resumable([], 'a.bin', 10) is None


def test_raise_for_api_error_is_a_noop_for_ok_response():
    filetransfer._raise_for_api_error(FakeResponse(200))


def test_raise_for_api_error_exits_on_error_response():
    with pytest.raises(typer.Exit):
        filetransfer._raise_for_api_error(FakeResponse(400, {'detail': 'bad request'}))


def test_raise_for_api_error_handles_non_json_body():
    with pytest.raises(typer.Exit):
        filetransfer._raise_for_api_error(FakeResponse(500, text='<html>gateway error</html>'))


def test_get_headers_requires_login(token_file):
    with pytest.raises(typer.Exit):
        filetransfer.get_headers()


def test_get_headers_returns_bearer_token(token_file):
    token_file.write_text('abc123')

    assert filetransfer.get_headers() == {'Authorization': 'Bearer abc123'}


# -- Upload logic (`_upload_file`), with every HTTP call mocked --


def test_upload_file_new_file_happy_path(tmp_path, monkeypatch):
    path = tmp_path / 'report.pdf'
    path.write_bytes(b'x' * 10)
    calls = []

    def fake_post(url, json=None, headers=None, **kwargs):
        calls.append(url)
        if url.endswith('/files/'):
            return FakeResponse(
                201, {'file_id': 'f1', 'part_size_bytes': 1024 * 1024, 'part_count': 1}
            )
        if url.endswith('/parts/'):
            return FakeResponse(200, {'urls': {'1': 'https://s3.example.com/put'}})
        if url.endswith('/complete/'):
            return FakeResponse(200, {'ok': True})
        raise AssertionError(f'unexpected POST {url}')

    def fake_put(url, data=None, headers=None, **kwargs):
        calls.append(url)
        return FakeResponse(200, headers={'ETag': 'etag-1'})

    monkeypatch.setattr('requests.post', fake_post)
    monkeypatch.setattr('requests.put', fake_put)

    filetransfer._upload_file({}, 'transfer-1', path, [])

    assert any(url.endswith('/files/') for url in calls)
    assert any(url == 'https://s3.example.com/put' for url in calls)
    assert any(url.endswith('/complete/') for url in calls)


def test_upload_file_skips_an_already_uploaded_match(tmp_path, monkeypatch):
    path = tmp_path / 'a.bin'
    path.write_bytes(b'x')
    existing = [{'id': 'f1', 'name': 'a.bin', 'size': 1, 'uploaded': True}]

    def fail(*args, **kwargs):
        raise AssertionError('no HTTP call expected for an already-uploaded file')

    monkeypatch.setattr('requests.post', fail)
    monkeypatch.setattr('requests.put', fail)

    filetransfer._upload_file({}, 'transfer-1', path, existing)


def test_upload_file_resumes_a_matching_incomplete_file(tmp_path, monkeypatch):
    path = tmp_path / 'report.pdf'
    path.write_bytes(b'x' * 20)
    existing = [{'id': 'f1', 'name': 'report.pdf', 'size': 20, 'uploaded': False}]
    calls = []

    def fake_post(url, json=None, headers=None, **kwargs):
        calls.append((url, json))
        if url.endswith('/resume/'):
            return FakeResponse(
                200,
                {
                    'restarted': False,
                    'part_size_bytes': 1024 * 1024,
                    'part_count': 1,
                    'uploaded_parts': [{'part_number': 1, 'etag': 'old-etag', 'size': 20}],
                },
            )
        if url.endswith('/complete/'):
            return FakeResponse(200, {'ok': True})
        raise AssertionError(f'unexpected POST {url}')

    def fail_put(*args, **kwargs):
        raise AssertionError('every part was already uploaded; nothing should be PUT')

    monkeypatch.setattr('requests.post', fake_post)
    monkeypatch.setattr('requests.put', fail_put)

    filetransfer._upload_file({}, 'transfer-1', path, existing)

    resume_calls = [c for c in calls if c[0].endswith('/resume/')]
    complete_calls = [c for c in calls if c[0].endswith('/complete/')]
    assert len(resume_calls) == 1
    assert complete_calls[0][1] == {'parts': [{'part_number': 1, 'etag': 'old-etag'}]}


def test_upload_file_uploads_only_the_missing_parts_when_resuming(tmp_path, monkeypatch):
    part_size = 5
    path = tmp_path / 'a.bin'
    path.write_bytes(b'A' * part_size + b'B' * part_size)  # 2 parts
    existing = [{'id': 'f1', 'name': 'a.bin', 'size': part_size * 2, 'uploaded': False}]
    put_calls = []

    def fake_post(url, json: dict | None = None, headers=None, **kwargs):
        if url.endswith('/resume/'):
            return FakeResponse(
                200,
                {
                    'restarted': False,
                    'part_size_bytes': part_size,
                    'part_count': 2,
                    'uploaded_parts': [{'part_number': 1, 'etag': 'old-etag-1', 'size': part_size}],
                },
            )
        assert json is not None
        if url.endswith('/parts/'):
            assert json['parts'] == [
                {'part_number': 2, 'checksum_sha256': filetransfer._sha256_base64(b'B' * part_size)}
            ]
            return FakeResponse(200, {'urls': {'2': 'https://s3.example.com/put2'}})
        if url.endswith('/complete/'):
            assert sorted(json['parts'], key=lambda p: p['part_number']) == [
                {'part_number': 1, 'etag': 'old-etag-1'},
                {
                    'part_number': 2,
                    'etag': 'new-etag-2',
                    'checksum_sha256': filetransfer._sha256_base64(b'B' * part_size),
                },
            ]
            return FakeResponse(200, {'ok': True})
        raise AssertionError(f'unexpected POST {url}')

    def fake_put(url, data=None, headers=None, **kwargs):
        put_calls.append((url, data))
        return FakeResponse(200, headers={'ETag': 'new-etag-2'})

    monkeypatch.setattr('requests.post', fake_post)
    monkeypatch.setattr('requests.put', fake_put)

    filetransfer._upload_file({}, 'transfer-1', path, existing)

    assert put_calls == [('https://s3.example.com/put2', b'B' * part_size)]


def test_upload_file_restarted_upload_reuploads_everything(tmp_path, monkeypatch):
    path = tmp_path / 'a.bin'
    path.write_bytes(b'x' * 10)
    existing = [{'id': 'f1', 'name': 'a.bin', 'size': 10, 'uploaded': False}]

    def fake_post(url, json=None, headers=None, **kwargs):
        if url.endswith('/resume/'):
            return FakeResponse(
                200,
                {'restarted': True, 'part_size_bytes': 1024, 'part_count': 1, 'uploaded_parts': []},
            )
        if url.endswith('/parts/'):
            return FakeResponse(200, {'urls': {'1': 'https://s3.example.com/put'}})
        if url.endswith('/complete/'):
            return FakeResponse(200, {'ok': True})
        raise AssertionError(f'unexpected POST {url}')

    monkeypatch.setattr('requests.post', fake_post)
    monkeypatch.setattr(
        'requests.put', lambda *a, **k: FakeResponse(200, headers={'ETag': 'etag-1'})
    )

    filetransfer._upload_file({}, 'transfer-1', path, existing)


# -- Command wiring (typer CliRunner) --


def test_login_success_saves_token(token_file, monkeypatch):
    monkeypatch.setattr(
        'requests.post', lambda *a, **k: FakeResponse(200, {'access_token': 'tok123'})
    )

    result = runner.invoke(filetransfer.app, ['login', 'dev', 'me@example.com', 'pw'])

    assert result.exit_code == 0
    assert token_file.read_text().strip() == 'tok123'


def test_login_failure_exits_nonzero(token_file, monkeypatch):
    import requests

    def fake_post(*args, **kwargs):
        raise requests.exceptions.RequestException('boom')

    monkeypatch.setattr('requests.post', fake_post)

    result = runner.invoke(filetransfer.app, ['login', 'dev', 'me@example.com', 'pw'])

    assert result.exit_code == 1
    assert not token_file.exists()


def test_list_with_no_transfers(token_file, monkeypatch):
    token_file.write_text('tok')
    monkeypatch.setattr(
        'requests.get',
        lambda *a, **k: FakeResponse(200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []}),
    )

    result = runner.invoke(filetransfer.app, ['list', 'dev'])

    assert result.exit_code == 0


def test_list_without_login_exits_nonzero(token_file):
    result = runner.invoke(filetransfer.app, ['list', 'dev'])

    assert result.exit_code == 1


def test_send_with_no_matching_files_exits_nonzero(token_file, tmp_path):
    token_file.write_text('tok')

    result = runner.invoke(
        filetransfer.app,
        ['send', 'dev', str(tmp_path / 'missing.bin'), '--to', 'a@example.com'],
    )

    assert result.exit_code == 1


def test_disable_command_patches_transfer(token_file, monkeypatch):
    token_file.write_text('tok')
    seen = {}

    def fake_patch(url, json=None, headers=None, **kwargs):
        seen['url'] = url
        seen['json'] = json
        return FakeResponse(200, {'status': 'disabled'})

    monkeypatch.setattr('requests.patch', fake_patch)

    result = runner.invoke(filetransfer.app, ['disable', 'dev', 'transfer-1'])

    assert result.exit_code == 0
    assert seen['url'].endswith('/api/ft/transfers/transfer-1')
    assert seen['json'] == {'disabled': True}


def test_delete_command(token_file, monkeypatch):
    token_file.write_text('tok')
    monkeypatch.setattr('requests.delete', lambda *a, **k: FakeResponse(204))

    result = runner.invoke(filetransfer.app, ['delete', 'dev', 'transfer-1'])

    assert result.exit_code == 0
