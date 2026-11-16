"""Unit tests for the file request commands of `admin/filetransfer.py` (issue #306): HTTP mocked,
no Django, no database."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from admin import filetransfer

from .test_filetransfer import FakeResponse

pytestmark = pytest.mark.unit

runner = CliRunner()

REQUEST: dict[str, Any] = {
    'id': '11111111-1111-1111-1111-111111111111',
    'status': 'active',
    'title': 'Wedding photos',
    'message': 'Send yours',
    'upload_audience': 'selected',
    'max_files': None,
    'max_total_size_bytes': None,
    'effective_max_files': 100,
    'effective_max_total_size_bytes': 21474836480,
    'is_open': True,
    'done_at': None,
    'done_by': '',
    'expires_at': None,
    'has_password': False,
    'size_bytes': 2048,
    'file_count': 2,
    'uploader_count': 2,
    'download_count': 0,
    'credits_charged': 0,
    'url': 'https://app.example/t/abc/',
    'created_at': '2026-11-14T10:00:00Z',
    'files': [
        {
            'id': 'f1',
            'name': 'photo.jpg',
            'size': 1024,
            'uploaded': True,
            'uploaded_at': '2026-11-14T10:05:00Z',
            'uploader_name': 'Jane',
            'uploader_email': '',
            'created_at': None,
        },
        {
            'id': 'f2',
            'name': 'a/b.jpg',
            'size': 1024,
            'uploaded': True,
            'uploaded_at': '2026-11-14T10:06:00Z',
            'uploader_name': 'John',
            'uploader_email': 'john@example.com',
            'created_at': None,
        },
    ],
    'invites': [{'id': 'i1', 'email': 'a@example.com', 'last_sent_at': None}],
}


@pytest.fixture(autouse=True)
def _logged_in(token_dir):
    (token_dir / '.filetransfer_token.local').write_text('tok')


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(filetransfer.time, 'sleep', lambda *args, **kwargs: None)


@dataclass
class Calls:
    made: list[tuple[str, str, dict]] = field(default_factory=list)
    answers: dict[tuple[str, str], FakeResponse] = field(default_factory=dict)


@pytest.fixture
def calls(monkeypatch):
    """Record every request the CLI makes and answer with whatever `answers` says."""
    recorded = Calls()
    made = recorded.made
    answers = recorded.answers

    def fake(method):
        def _call(url, **kwargs):
            made.append((method, url, kwargs))
            for (m, suffix), response in answers.items():
                if m == method and url.endswith(suffix):
                    return response
            raise AssertionError(f'unexpected {method.upper()} {url}')

        return _call

    for method in ('get', 'post', 'patch', 'delete'):
        monkeypatch.setattr(f'requests.{method}', fake(method))
    return recorded


def _invoke(*args, **kwargs):
    return runner.invoke(filetransfer.app, list(args), **kwargs)


# -- request-create --


def test_create_an_anyone_request(calls):
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(201, REQUEST)

    result = _invoke('request-create', 'local', 'Wedding photos', '-m', 'Send yours')

    assert result.exit_code == 0
    _method, url, kwargs = calls.made[0]
    assert url.endswith('/api/ft/requests')
    assert kwargs['json'] == {
        'title': 'Wedding photos',
        'message': 'Send yours',
        'expiry_choice': 'none',
    }
    assert kwargs['headers']['Authorization'] == 'Bearer tok'


def test_create_without_a_title_sends_a_blank_one(calls):
    """Issue #318: the title is optional."""
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(201, {**REQUEST, 'title': ''})
    result = _invoke('request-create', 'local', '--signed-in')
    assert result.exit_code == 0
    assert calls.made[0][2]['json']['title'] == ''


def test_a_request_with_no_title_is_listed_as_untitled(calls):
    calls.answers[('get', '/api/ft/requests')] = FakeResponse(
        200, {'count': 1, 'limit': 20, 'offset': 0, 'results': [{**REQUEST, 'title': ''}]}
    )
    result = _invoke('requests', 'local', env={'COLUMNS': '200'})
    assert result.exit_code == 0 and '(untitled)' in result.output


def test_create_prints_the_id_and_the_link(calls, caplog):
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(201, REQUEST)
    with caplog.at_level('INFO'):
        _invoke('request-create', 'local', 'Wedding photos')
    assert REQUEST['id'] in caplog.text and 'https://app.example/t/abc/' in caplog.text


def test_create_for_selected_people_with_limits_and_expiry(calls):
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(201, REQUEST)

    result = _invoke(
        'request-create',
        'local',
        'Contracts',
        '--to',
        'a@example.com',
        '--to',
        'b@example.com',
        '--max-files',
        '10',
        '--max-gb',
        '1.5',
        '--expires',
        '30',
    )

    assert result.exit_code == 0
    payload = calls.made[0][2]['json']
    assert payload['upload_audience'] == 'selected'
    assert payload['emails'] == ['a@example.com', 'b@example.com']
    assert payload['max_files'] == 10 and payload['max_total_size_bytes'] == 1610612736
    assert payload['expiry_choice'] == '30'


def test_create_signed_in_with_a_date_expiry(calls):
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(201, REQUEST)
    _invoke('request-create', 'local', 'x', '--signed-in', '--expires', '2027-12-31')
    payload = calls.made[0][2]['json']
    assert payload['upload_audience'] == 'signed_in'
    assert payload['expiry_choice'] == 'custom' and payload['expiry_date'] == '2027-12-31'


def test_create_refuses_conflicting_audiences_and_bad_expiry(calls):
    assert _invoke('request-create', 'local', 'x', '--anyone', '--signed-in').exit_code != 0
    assert _invoke('request-create', 'local', 'x', '--signed-in', '--to', 'a@x.org').exit_code != 0
    assert _invoke('request-create', 'local', 'x', '--expires', 'next week').exit_code != 0
    assert calls.made == []


def test_create_password_is_read_from_the_environment_never_an_argument(calls, monkeypatch):
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(201, REQUEST)
    monkeypatch.setenv('FILETRANSFER_LINK_PASSWORD', 'sekret')
    _invoke('request-create', 'local', 'x', '--password')
    assert calls.made[0][2]['json']['password'] == 'sekret'


def test_create_password_prompts_hidden_without_the_variable(calls, monkeypatch):
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(201, REQUEST)
    monkeypatch.delenv('FILETRANSFER_LINK_PASSWORD', raising=False)
    seen = {}

    def prompt(text, **kwargs):
        seen.update(kwargs)
        return 'typed'

    monkeypatch.setattr(filetransfer.typer, 'prompt', prompt)
    # `utils.password_from_prompt_or_env` imports typer itself:
    monkeypatch.setattr('admin.utils.typer.prompt', prompt)
    _invoke('request-create', 'local', 'x', '--password')
    assert calls.made[0][2]['json']['password'] == 'typed' and seen['hide_input'] is True


def test_create_without_password_sends_none(calls):
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(201, REQUEST)
    _invoke('request-create', 'local', 'x')
    assert 'password' not in calls.made[0][2]['json']


@pytest.mark.parametrize(
    ('status', 'detail'),
    [
        (400, 'title: Keep the title to 100 characters or fewer.'),
        (402, 'You need at least 1 credit to request files.'),
        (403, "Sorry, you can't send transfers at this time."),
    ],
)
def test_create_errors_exit_1_with_the_api_message(calls, caplog, status, detail):
    calls.answers[('post', '/api/ft/requests')] = FakeResponse(status, {'detail': detail})
    result = _invoke('request-create', 'local', 'x')
    assert result.exit_code == 1 and detail in caplog.text


# -- requests / request-get --


def test_requests_lists_with_full_ids(calls):
    done = {
        **REQUEST,
        'id': '2' * 8 + '-2222-2222-2222-222222222222',
        'is_open': False,
        'title': '[bold]x',
    }
    calls.answers[('get', '/api/ft/requests')] = FakeResponse(
        200, {'count': 2, 'limit': 20, 'offset': 0, 'results': [REQUEST, done]}
    )
    result = runner.invoke(
        filetransfer.app, ['requests', 'local', '--filter', 'all'], env={'COLUMNS': '200'}
    )

    assert result.exit_code == 0
    assert calls.made[0][2]['params'] == {'filter': 'all', 'limit': 20, 'offset': 0}
    assert REQUEST['id'] in result.output and done['id'] in result.output
    assert 'Wedding photos' in result.output and '[bold]x' in result.output  # markup shown as typed
    assert 'open' in result.output and 'done' in result.output


def test_requests_empty_and_paging(calls, caplog):
    calls.answers[('get', '/api/ft/requests')] = FakeResponse(
        200, {'count': 0, 'limit': 20, 'offset': 0, 'results': []}
    )
    with caplog.at_level('INFO'):
        _invoke('requests', 'local')
    assert 'No requests found.' in caplog.text

    calls.answers[('get', '/api/ft/requests')] = FakeResponse(
        200, {'count': 5, 'limit': 1, 'offset': 0, 'results': [REQUEST]}
    )
    with caplog.at_level('INFO'):
        _invoke('requests', 'local', '--limit', '1')
    assert 'Use --offset 1 to see more.' in caplog.text


def test_request_get_prints_everything(calls):
    calls.answers[('get', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(200, REQUEST)
    result = _invoke('request-get', 'local', REQUEST['id'])

    assert result.exit_code == 0
    for text in (
        'Wedding photos',
        'open (active)',
        'only the invited people',
        'a@example.com',
        'photo.jpg',
        'Jane',
        'John <john@example.com>',
        'a/b.jpg',
        'https://app.example/t/abc/',
        '2 uploaders',
    ):
        assert text in result.output, text


def test_request_get_a_done_request_says_by_whom(calls):
    done = {**REQUEST, 'is_open': False, 'done_at': '2026-11-14T11:00:00Z', 'done_by': 'Jane'}
    calls.answers[('get', f'/api/ft/requests/{done["id"]}')] = FakeResponse(200, done)
    result = _invoke('request-get', 'local', done['id'])
    assert 'done (active)' in result.output and 'by Jane' in result.output


def test_request_get_404_exits_1(calls, caplog):
    calls.answers[('get', '/api/ft/requests/nope')] = FakeResponse(404, {'detail': 'Not Found'})
    assert _invoke('request-get', 'local', 'nope').exit_code == 1
    assert '404: Not Found' in caplog.text


# -- request-edit / done / reopen / delete --


def test_edit_sends_only_what_is_passed(calls):
    calls.answers[('patch', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(200, REQUEST)
    result = _invoke(
        'request-edit',
        'local',
        REQUEST['id'],
        '--title',
        'New',
        '--max-files',
        '5',
        '--expires',
        '15',
    )
    assert result.exit_code == 0
    assert calls.made[0][2]['json'] == {'title': 'New', 'max_files': 5, 'expiry_choice': '15'}


def test_edit_clears_limits_and_replaces_invitees_and_password(calls, monkeypatch):
    calls.answers[('patch', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(200, REQUEST)
    _invoke(
        'request-edit',
        'local',
        REQUEST['id'],
        '--clear-max-files',
        '--clear-max-gb',
        '--to',
        'x@example.com',
        '--remove-password',
    )
    assert calls.made[0][2]['json'] == {
        'max_files': None,
        'max_total_size_bytes': None,
        'upload_audience': 'selected',
        'emails': ['x@example.com'],
        'remove_password': True,
    }


def test_edit_sets_a_password_from_the_environment(calls, monkeypatch):
    calls.answers[('patch', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(200, REQUEST)
    monkeypatch.setenv('FILETRANSFER_LINK_PASSWORD', 'sekret')
    _invoke('request-edit', 'local', REQUEST['id'], '--password')
    assert calls.made[0][2]['json'] == {'password': 'sekret'}


def test_edit_with_nothing_to_change_or_conflicts_sends_nothing(calls):
    assert _invoke('request-edit', 'local', REQUEST['id']).exit_code == 1
    assert (
        _invoke(
            'request-edit', 'local', REQUEST['id'], '--max-files', '3', '--clear-max-files'
        ).exit_code
        != 0
    )
    assert (
        _invoke('request-edit', 'local', REQUEST['id'], '--password', '--remove-password').exit_code
        != 0
    )
    assert _invoke('request-edit', 'local', REQUEST['id'], '--anyone', '--signed-in').exit_code != 0
    assert calls.made == []


def test_edit_error_is_reported(calls, caplog):
    calls.answers[('patch', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(
        400, {'detail': 'max_files: Already 2 files uploaded; choose at least 2.'}
    )
    assert _invoke('request-edit', 'local', REQUEST['id'], '--max-files', '1').exit_code == 1
    assert 'Already 2 files uploaded' in caplog.text


def test_done_and_reopen(calls):
    calls.answers[('post', f'/api/ft/requests/{REQUEST["id"]}/done')] = FakeResponse(200, REQUEST)
    calls.answers[('post', f'/api/ft/requests/{REQUEST["id"]}/reopen')] = FakeResponse(200, REQUEST)
    assert _invoke('request-done', 'local', REQUEST['id']).exit_code == 0
    assert _invoke('request-reopen', 'local', REQUEST['id']).exit_code == 0
    assert [url.rsplit('/', 1)[-1] for _m, url, _k in calls.made] == ['done', 'reopen']


def test_reopen_an_open_request_reports_the_error(calls, caplog):
    calls.answers[('post', f'/api/ft/requests/{REQUEST["id"]}/reopen')] = FakeResponse(
        400, {'detail': 'This request is already open.'}
    )
    assert _invoke('request-reopen', 'local', REQUEST['id']).exit_code == 1
    assert 'already open' in caplog.text


def test_delete_asks_first(calls):
    calls.answers[('delete', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(204)
    declined = _invoke('request-delete', 'local', REQUEST['id'], input='n\n')
    assert declined.exit_code == 1 and calls.made == []

    accepted = _invoke('request-delete', 'local', REQUEST['id'], input='y\n')
    assert accepted.exit_code == 0 and calls.made[0][0] == 'delete'


def test_delete_yes_skips_the_question(calls):
    calls.answers[('delete', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(204)
    assert _invoke('request-delete', 'local', REQUEST['id'], '--yes').exit_code == 0


def test_delete_a_held_request_reports_409(calls, caplog):
    calls.answers[('delete', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(
        409, {'detail': 'This transfer is on hold pending review and cannot be changed right now.'}
    )
    assert _invoke('request-delete', 'local', REQUEST['id'], '-y').exit_code == 1
    assert 'on hold' in caplog.text


# -- request-download --


class FakeStream:
    def __init__(self, chunks, fail_after=None):
        self.chunks = chunks
        self.fail_after = fail_after
        self.ok = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=0):
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index >= self.fail_after:
                import requests

                raise requests.exceptions.ConnectionError('dropped')
            yield chunk


@pytest.fixture
def downloads(calls, monkeypatch):
    """Answer the request and a presigned URL per file; the bytes come from `streams`."""
    calls.answers[('get', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(200, REQUEST)
    for file in REQUEST['files']:
        calls.answers[('get', f'/files/{file["id"]}/url')] = FakeResponse(
            200,
            {
                'url': f'https://s3.example/{file["id"]}',
                'filename': file['name'],
                'expires_in': 900,
            },
        )
    streams = {
        'https://s3.example/f1': FakeStream([b'one', b'-1']),
        'https://s3.example/f2': FakeStream([b'two']),
    }
    streamed = []

    def fake_get(url, **kwargs):
        if url.startswith('https://s3.example/'):
            streamed.append((url, kwargs))
            return streams[url]
        return calls_get(url, **kwargs)

    calls_get = __import__('requests').get
    # the `calls` fixture replaced requests.get; route the S3 URLs to the streams.
    original = calls_get

    def routed(url, **kwargs):
        if url.startswith('https://s3.example/'):
            streamed.append((url, kwargs))
            return streams[url]
        return original(url, **kwargs)

    monkeypatch.setattr('requests.get', routed)
    return type('D', (), {'streams': streams, 'streamed': streamed})()


def test_download_writes_every_file_through_a_part_file(downloads, tmp_path):
    out = tmp_path / 'photos'
    result = _invoke('request-download', 'local', REQUEST['id'], '--out', str(out))

    assert result.exit_code == 0
    assert (out / 'photo.jpg').read_bytes() == b'one-1'
    assert (out / 'a_b.jpg').read_bytes() == b'two'  # no path separators from an uploader's name
    assert sorted(p.name for p in out.iterdir()) == ['a_b.jpg', 'photo.jpg']  # no .part left
    # The presigned URL is fetched without the API token.
    assert all('Authorization' not in (k.get('headers') or {}) for _u, k in downloads.streamed)


def test_download_refuses_to_overwrite_without_force(downloads, tmp_path):
    (tmp_path / 'photo.jpg').write_bytes(b'mine')
    result = _invoke('request-download', 'local', REQUEST['id'], '--out', str(tmp_path))
    assert result.exit_code == 1
    assert (tmp_path / 'photo.jpg').read_bytes() == b'mine'
    assert not downloads.streamed  # nothing was downloaded at all

    forced = _invoke('request-download', 'local', REQUEST['id'], '--out', str(tmp_path), '--force')
    assert forced.exit_code == 0 and (tmp_path / 'photo.jpg').read_bytes() == b'one-1'


def test_download_a_dropped_connection_leaves_no_cut_off_file(downloads, tmp_path):
    downloads.streams['https://s3.example/f1'] = FakeStream([b'one', b'-1'], fail_after=1)
    result = _invoke('request-download', 'local', REQUEST['id'], '--out', str(tmp_path))
    assert result.exit_code == 1
    assert not (tmp_path / 'photo.jpg').exists()
    assert not list(tmp_path.glob('.*.part'))
    assert (tmp_path / 'a_b.jpg').read_bytes() == b'two'  # the others still arrive


def test_download_with_nothing_uploaded(calls, tmp_path, caplog):
    calls.answers[('get', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(
        200, {**REQUEST, 'files': []}
    )
    with caplog.at_level('INFO'):
        result = _invoke('request-download', 'local', REQUEST['id'], '--out', str(tmp_path / 'x'))
    assert result.exit_code == 0 and 'Nothing has been uploaded yet.' in caplog.text
    assert not (tmp_path / 'x').exists()


def test_download_names_that_collide_are_numbered(calls, monkeypatch, tmp_path):
    twins = [{**REQUEST['files'][0], 'id': 'a1'}, {**REQUEST['files'][0], 'id': 'a2'}]
    calls.answers[('get', f'/api/ft/requests/{REQUEST["id"]}')] = FakeResponse(
        200, {**REQUEST, 'files': twins}
    )
    for file in twins:
        calls.answers[('get', f'/files/{file["id"]}/url')] = FakeResponse(
            200,
            {'url': f'https://s3.example/{file["id"]}', 'filename': 'photo.jpg', 'expires_in': 9},
        )
    original = __import__('requests').get
    monkeypatch.setattr(
        'requests.get',
        lambda url, **kw: (
            FakeStream([url.encode()]) if url.startswith('https://s3') else original(url, **kw)
        ),
    )
    out = tmp_path / 'out'
    assert _invoke('request-download', 'local', REQUEST['id'], '--out', str(out)).exit_code == 0
    assert sorted(p.name for p in out.iterdir()) == ['photo (1).jpg', 'photo.jpg']


def test_local_name_is_safe_and_unique():
    used: set[str] = set()
    assert filetransfer._local_name('../../etc/passwd', used) == '_.._etc_passwd'
    assert filetransfer._local_name('', used) == 'file'
    assert filetransfer._local_name('a\\b.txt', used) == 'a_b.txt'
    assert filetransfer._local_name('a_b.txt', used) == 'a_b (1).txt'
    assert filetransfer._local_name('.hidden', used) == 'hidden'
