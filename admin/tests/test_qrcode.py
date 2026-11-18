"""Unit tests for `admin/qrcode.py`'s command wiring and for `admin/utils.write_private_file`. No
Django, no database, no network (`requests` is mocked); the shared token/site helpers are tested
in `test_cli_utils.py`."""

import json

import pytest
from typer.testing import CliRunner

from admin import qrcode, utils

pytestmark = pytest.mark.unit

runner = CliRunner()


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_login_takes_the_password_from_the_prompt_not_the_command_line(token_dir, monkeypatch):
    seen = {}

    def fake_post(url, json=None, **kwargs):
        seen.update(url=url, json=json)
        return FakeResponse({'access_token': 'tok'})

    monkeypatch.setattr('requests.post', fake_post)
    monkeypatch.setattr(qrcode.typer, 'prompt', lambda *a, **k: 'typed-pw')

    result = runner.invoke(qrcode.app, ['login', 'local', 'me@example.com'])

    assert result.exit_code == 0
    assert seen['json'] == {'email': 'me@example.com', 'password': 'typed-pw'}
    assert (token_dir / '.qrcode_token.local').read_text() == 'tok'


def test_login_reads_the_password_from_the_env_var(token_dir, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        'requests.post',
        lambda url, json=None, **k: seen.update(json=json) or FakeResponse({'access_token': 't'}),
    )
    monkeypatch.setenv('QRCODE_PASSWORD', 'env-pw')

    assert runner.invoke(qrcode.app, ['login', 'local', 'me@example.com']).exit_code == 0
    assert seen['json']['password'] == 'env-pw'


def test_commands_talk_to_the_selected_environments_site_with_its_token(
    token_dir, loads_base_url, monkeypatch
):
    """Both the site and the token are the selected environment's, read after its `.env.*` file is
    loaded -- not the local server's, as when `API_BASE_URL` was read at import time."""
    (token_dir / '.qrcode_token.local').write_text('local-token')
    (token_dir / '.qrcode_token.prod').write_text('prod-token')
    seen = {}

    def fake_get(url, headers=None, **kwargs):
        seen.update(url=url, headers=headers)
        return FakeResponse([])

    monkeypatch.setattr('requests.get', fake_get)

    assert runner.invoke(qrcode.app, ['list', 'prod']).exit_code == 0

    assert seen['url'] == 'https://prod.example/api/qr/'
    assert seen['headers'] == {'Authorization': 'Bearer prod-token'}


def test_a_token_from_an_older_version_is_ignored(token_dir):
    (token_dir / '.qrcode_token').write_text('legacy')

    assert runner.invoke(qrcode.app, ['list', 'local']).exit_code == 1


def test_write_private_file_replaces_longer_content(tmp_path):
    path = tmp_path / 'f'
    path.write_text('a much longer previous token')

    utils.write_private_file(path, 'short')

    assert path.read_text() == 'short'


def test_write_private_file_refuses_to_follow_a_symlink(tmp_path):
    target = tmp_path / 'target'
    target.write_text('keep')
    link = tmp_path / 'link'
    link.symlink_to(target)

    with pytest.raises(OSError):
        utils.write_private_file(link, 'secret')

    assert target.read_text() == 'keep'


# -- Share links ---------------------------------------------------------------------------------

QR_ID = '11111111-1111-1111-1111-111111111111'
LINK_ID = '44444444-4444-4444-4444-444444444444'
PERSON_ID = '55555555-5555-5555-5555-555555555555'
LINK = {
    'id': LINK_ID,
    'url': 'https://app.example.com/q/abc',
    'label': 'for Sam',
    'expires_at': None,
    'revoked_at': None,
    'active': True,
    'recipients': [],
    'created_at': '2026-11-09T09:00:00Z',
}
EMAILED = {
    **LINK,
    'recipients': [
        {'id': PERSON_ID, 'email': 'sam@example.com', 'last_sent_at': '2026-11-09T09:01:00Z'}
    ],
}


class ApiResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.headers = headers or {}
        self.ok = 200 <= status_code < 400
        self.text = json.dumps(self._json)

    def close(self):
        pass

    def json(self):
        return self._json


class Api:
    """Records every request and answers from a table of `(method, path) -> response`; a path is
    relative to `/api` (`/qr/<id>/links`)."""

    def __init__(self, monkeypatch):
        self.calls: list[dict] = []
        self.routes: dict[tuple[str, str], ApiResponse] = {}
        for method in ('get', 'post', 'patch', 'delete'):
            monkeypatch.setattr(f'requests.{method}', self._handler(method))

    def _handler(self, method):
        def handle(url, **kwargs):
            self.calls.append({'method': method, 'url': url, **kwargs})
            path = url.removeprefix('http://localhost:8000/api')
            try:
                return self.routes[(method, path)]
            except KeyError:
                raise AssertionError(f'unexpected {method.upper()} {path}') from None

        return handle

    def on(self, method, path, response):
        self.routes[(method, path)] = response
        return self

    def writes(self) -> list[dict]:
        return [c for c in self.calls if c['method'] != 'get']


@pytest.fixture
def api(monkeypatch, token_dir):
    (token_dir / '.qrcode_token.local').write_text('tok')
    return Api(monkeypatch)


def invoke(*args, input=None):
    return runner.invoke(qrcode.app, [*args], input=input)


def flat(result) -> str:
    """The output with the logger's line wrapping undone."""
    return ' '.join(result.output.split())


LINKS_PATH = f'/qr/{QR_ID}/links'


def test_links_lists_them_with_their_state_and_people(api):
    revoked = {**LINK, 'id': 'r' * 8, 'revoked_at': '2026-11-10T09:00:00Z', 'active': False}
    expired = {**LINK, 'id': 'e' * 8, 'expires_at': '2026-11-01T09:00:00Z', 'active': False}
    api.on('get', LINKS_PATH, ApiResponse(200, [EMAILED, revoked, expired]))

    result = invoke('links', 'local', QR_ID)

    assert result.exit_code == 0
    out = result.output
    assert 'active' in out and 'revoked' in out and 'expired' in out
    assert 'https://app.example.com/q/abc' in out and 'for Sam' in out
    assert 'sam@example.com' in out and '2026-11-01' in out
    assert api.calls[0]['headers'] == {'Authorization': 'Bearer tok'}


def test_links_with_none(api):
    api.on('get', LINKS_PATH, ApiResponse(200, []))

    assert 'No share links' in invoke('links', 'local', QR_ID).output


def test_links_shows_user_text_as_typed(api):
    hostile = {**LINK, 'label': '[bold]x[/bold]'}
    api.on('get', LINKS_PATH, ApiResponse(200, [hostile]))

    assert '[bold]x[/bold]' in invoke('links', 'local', QR_ID).output


def test_links_for_another_users_code_is_a_404_exit(api):
    api.on('get', LINKS_PATH, ApiResponse(404, {'detail': 'Not Found'}))

    result = invoke('links', 'local', QR_ID)

    assert result.exit_code == 1 and '404' in flat(result)


def test_share_makes_a_link_and_prints_its_url(api):
    api.on('post', LINKS_PATH, ApiResponse(201, LINK))

    result = invoke('share', 'local', QR_ID)

    assert result.exit_code == 0
    assert api.writes()[0]['json'] == {}
    assert 'Made a share link: https://app.example.com/q/abc' in result.output
    assert f'Link ID: {LINK_ID}' in result.output
    assert len(api.writes()) == 1  # no email without --to


def test_share_with_a_label_and_a_date(api):
    api.on('post', LINKS_PATH, ApiResponse(201, LINK))

    invoke('share', 'local', QR_ID, '--label', 'for Sam', '--expires', '2026-12-31')

    assert api.writes()[0]['json'] == {
        'label': 'for Sam',
        'expires_at': '2026-12-31',  # a bare date: the server reads it as the end of that day
    }


def test_share_with_a_date_time(api):
    api.on('post', LINKS_PATH, ApiResponse(201, LINK))

    invoke('share', 'local', QR_ID, '--expires', '2026-12-31 18:00')

    assert api.writes()[0]['json']['expires_at'] == '2026-12-31T18:00:00'


def test_share_refuses_a_bad_date_before_calling(api):
    result = invoke('share', 'local', QR_ID, '--expires', 'soon')

    assert result.exit_code == 2 and api.calls == []


def test_share_with_to_emails_the_link(api):
    api.on('post', LINKS_PATH, ApiResponse(201, LINK))
    api.on('post', f'{LINKS_PATH}/{LINK_ID}/email', ApiResponse(200, EMAILED))

    result = invoke(
        'share', 'local', QR_ID, '--to', 'sam@example.com', '--to', 'ann@example.com',
        '--message', 'Have a look',
    )  # fmt: skip

    assert result.exit_code == 0
    create, email = api.writes()
    assert create['json'] == {}
    assert email['json'] == {
        'emails': ['sam@example.com', 'ann@example.com'],
        'message': 'Have a look',
    }
    assert 'https://app.example.com/q/abc' in result.output
    assert 'Emailed the link to sam@example.com.' in result.output


def test_share_message_without_to_is_a_usage_error(api):
    result = invoke('share', 'local', QR_ID, '--message', 'hi')

    assert result.exit_code == 2 and api.calls == []


def test_share_when_the_email_is_refused_still_prints_the_link(api):
    api.on('post', LINKS_PATH, ApiResponse(201, LINK))
    detail = 'Confirm your email address to send a QR code by email.'
    api.on('post', f'{LINKS_PATH}/{LINK_ID}/email', ApiResponse(400, {'detail': detail}))

    result = invoke('share', 'local', QR_ID, '--to', 'sam@example.com')

    assert result.exit_code == 1
    assert 'https://app.example.com/q/abc' in result.output
    assert 'Confirm your email address' in flat(result) and 'not emailed' in flat(result)


def test_share_a_refusal(api):
    detail = 'A QR code can have at most 10 active links.'
    api.on('post', LINKS_PATH, ApiResponse(400, {'detail': detail}))

    result = invoke('share', 'local', QR_ID)

    assert result.exit_code == 1 and 'at most 10 active links' in flat(result)


def test_share_a_taken_down_code_says_so(api):
    detail = 'This QR code was taken down and cannot be shared.'
    api.on('post', LINKS_PATH, ApiResponse(409, {'detail': detail}))

    result = invoke('share', 'local', QR_ID)

    assert result.exit_code == 1 and 'taken down' in flat(result)


def test_share_a_rate_limit_tells_when_to_try_again(api):
    api.on(
        'post',
        LINKS_PATH,
        ApiResponse(429, {'detail': 'Too many requests'}, headers={'Retry-After': '3000'}),
    )

    result = invoke('share', 'local', QR_ID)

    assert result.exit_code == 1 and 'try again in 50 minutes' in flat(result)


def test_unshare_asks_first(api):
    api.on('delete', f'{LINKS_PATH}/{LINK_ID}', ApiResponse(204))

    declined = invoke('unshare', 'local', QR_ID, LINK_ID, input='n\n')
    assert declined.exit_code == 1 and api.writes() == []

    result = invoke('unshare', 'local', QR_ID, LINK_ID, input='y\n')
    assert result.exit_code == 0 and 'Revoked the link.' in result.output
    assert invoke('unshare', 'local', QR_ID, LINK_ID, '--yes').exit_code == 0
    assert len(api.writes()) == 2


def test_unshare_an_unknown_link_is_a_404_exit(api):
    api.on('delete', f'{LINKS_PATH}/{LINK_ID}', ApiResponse(404, {'detail': 'Not Found'}))

    assert invoke('unshare', 'local', QR_ID, LINK_ID, '--yes').exit_code == 1


def test_the_share_commands_need_a_token(token_dir, monkeypatch):
    calls = []
    monkeypatch.setattr('requests.get', lambda *a, **k: calls.append(a))

    assert invoke('links', 'local', QR_ID).exit_code == 1 and calls == []
