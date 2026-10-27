"""Unit tests for the helpers the JWT HTTP-client CLIs (`qrcode`, `filetransfer`, `receipts`, `shorturl`)
share in `admin/utils.py`: the site URL, one token file per CLI and environment, the password
prompt. Plain file-system and environment logic: no Django, no database, no network."""

import pytest
import typer

from admin import utils

pytestmark = pytest.mark.unit

CLIS = ['qrcode', 'filetransfer', 'receipts', 'shorturl']


# -- Site URL --


def test_api_url_defaults_to_the_local_server():
    assert utils.api_url('/qr/') == 'http://localhost:8000/api/qr/'


def test_api_url_uses_the_environment_files_base_url(monkeypatch):
    # Read at call time, so the value `set_environment` loads into `os.environ` is seen.
    monkeypatch.setenv('BASE_URL', 'https://example.com/')

    assert utils.api_url('/receipts/inbox') == 'https://example.com/api/receipts/inbox'
    assert utils.site_url() == 'https://example.com'

    monkeypatch.setenv('API_BASE_URL', 'http://override:9000')
    assert utils.api_url('/auth/login') == 'http://override:9000/api/auth/login'


def test_select_environment_makes_the_file_s_base_url_visible(loads_base_url):
    assert utils.site_url() == 'http://localhost:8000'

    utils.select_environment('prod')

    assert utils.site_url() == 'https://prod.example'


def test_select_environment_says_which_site_it_will_use(loads_base_url, capsys):
    utils.select_environment('prod')

    err = capsys.readouterr().err
    assert 'Site: https://prod.example (prod)' in err
    assert 'Warning' not in err


def test_a_stray_api_base_url_that_differs_from_the_env_file_is_warned_about(
    loads_base_url, monkeypatch, capsys
):
    monkeypatch.setenv('API_BASE_URL', 'http://elsewhere:9000/')

    utils.select_environment('prod')

    err = capsys.readouterr().err
    assert 'API_BASE_URL (http://elsewhere:9000) overrides the prod' in err
    assert 'Site: http://elsewhere:9000 (prod)' in err


def test_an_api_base_url_equal_to_the_env_file_is_not_a_warning(
    loads_base_url, monkeypatch, capsys
):
    monkeypatch.setenv('API_BASE_URL', 'https://prod.example/')

    utils.select_environment('prod')

    assert 'Warning' not in capsys.readouterr().err


# -- Token files --


@pytest.mark.parametrize('cli', CLIS)
def test_get_headers_requires_login(cli):
    with pytest.raises(typer.Exit):
        utils.get_headers(cli)


@pytest.mark.parametrize('cli', CLIS)
def test_get_headers_returns_bearer_token(cli, token_dir):
    (token_dir / f'.{cli}_token.local').write_text('abc123\n')

    assert utils.get_headers(cli) == {'Authorization': 'Bearer abc123'}


@pytest.mark.parametrize('cli', CLIS)
def test_save_token_creates_file_with_owner_only_permissions(cli, token_dir):
    utils.save_token(cli, 'secret')

    path = token_dir / f'.{cli}_token.local'
    assert path.read_text() == 'secret'
    assert (path.stat().st_mode & 0o777) == 0o600
    assert utils.get_token(cli) == 'secret'


def test_save_token_tightens_a_file_that_already_exists_with_looser_permissions(token_dir):
    path = token_dir / '.qrcode_token.local'
    path.write_text('old')
    path.chmod(0o644)

    utils.save_token('qrcode', 'secret')

    assert path.read_text() == 'secret'
    assert (path.stat().st_mode & 0o777) == 0o600


def test_save_token_will_not_write_through_a_symlink(token_dir):
    target = token_dir / 'elsewhere'
    target.write_text('keep')
    (token_dir / '.qrcode_token.local').symlink_to(target)

    with pytest.raises(OSError):
        utils.save_token('qrcode', 'secret')

    assert target.read_text() == 'keep'


def test_each_cli_has_its_own_token_file(token_dir):
    utils.save_token('qrcode', 'qr-token')
    utils.save_token('filetransfer', 'ft-token')

    assert utils.get_token('qrcode') == 'qr-token'
    assert utils.get_token('filetransfer') == 'ft-token'
    assert utils.get_token('receipts') is None


def test_each_environment_has_its_own_token_file(token_dir):
    (token_dir / '.filetransfer_token.local').write_text('local-token')
    (token_dir / '.filetransfer_token.prod').write_text('prod-token')

    utils.select_environment('prod')
    assert utils.get_headers('filetransfer') == {'Authorization': 'Bearer prod-token'}
    assert utils.current_environment() == 'prod'

    utils.select_environment('local')
    assert utils.get_headers('filetransfer') == {'Authorization': 'Bearer local-token'}


@pytest.mark.parametrize('cli', CLIS)
def test_a_token_from_an_older_version_is_ignored(cli, token_dir):
    (token_dir / f'.{cli}_token').write_text('legacy')

    assert utils.get_token(cli) is None
    with pytest.raises(typer.Exit):
        utils.get_headers(cli)


# -- Password --


def test_password_argument_wins(monkeypatch):
    monkeypatch.setenv('X_PASSWORD', 'from-env')

    assert utils.password_from_prompt_or_env('typed', 'X_PASSWORD') == 'typed'


def test_password_falls_back_to_the_env_var(monkeypatch):
    monkeypatch.setenv('X_PASSWORD', 'from-env')

    assert utils.password_from_prompt_or_env(None, 'X_PASSWORD') == 'from-env'


def test_password_is_prompted_for_with_echo_hidden_when_nothing_else(monkeypatch):
    monkeypatch.delenv('X_PASSWORD', raising=False)
    seen = {}

    def fake_prompt(text, **kwargs):
        seen.update(kwargs)
        return 'typed-pw'

    monkeypatch.setattr(typer, 'prompt', fake_prompt)

    assert utils.password_from_prompt_or_env(None, 'X_PASSWORD') == 'typed-pw'
    assert seen == {'hide_input': True}


# -- `api_request` and the error text (used by `shorturl`) --


class _Response:
    def __init__(self, status_code=200, json_data=None, headers=None, text=''):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text
        self.ok = 200 <= status_code < 400
        self.closed = False

    def json(self):
        if self._json is None:
            raise ValueError('no json')
        return self._json

    def close(self):
        self.closed = True


def test_api_request_passes_a_default_timeout(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return _Response()

    monkeypatch.setattr('requests.get', fake_get)

    utils.api_request('get', 'https://example.com')
    utils.api_request('get', 'https://example.com', timeout=5)

    assert seen['timeout'] == 5


def test_api_request_retries_a_short_429_and_closes_the_discarded_response(monkeypatch):
    slept = []
    monkeypatch.setattr(utils.time, 'sleep', lambda s: slept.append(s))
    first, final = _Response(429, headers={'Retry-After': '3'}), _Response(200)
    responses = iter([first, final])
    monkeypatch.setattr('requests.post', lambda url, **kw: next(responses))

    assert utils.api_request('post', 'https://example.com') is final
    assert slept == [3.0] and first.closed and not final.closed


def test_api_request_waits_one_second_when_retry_after_is_missing(monkeypatch):
    slept = []
    monkeypatch.setattr(utils.time, 'sleep', lambda s: slept.append(s))
    responses = iter([_Response(429), _Response(200)])
    monkeypatch.setattr('requests.get', lambda url, **kw: next(responses))

    assert utils.api_request('get', 'https://example.com').status_code == 200
    assert slept == [1.0]


def test_api_request_gives_up_after_the_max_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(utils.time, 'sleep', lambda s: None)
    monkeypatch.setattr('requests.get', lambda url, **kw: calls.append(1) or _Response(429))

    assert utils.api_request('get', 'https://example.com').status_code == 429
    assert len(calls) == utils.MAX_RATE_LIMIT_RETRIES + 1


def test_api_request_does_not_sleep_on_a_long_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(utils.time, 'sleep', lambda s: slept.append(s))
    monkeypatch.setattr(
        'requests.get', lambda url, **kw: _Response(429, headers={'Retry-After': '3000'})
    )

    assert utils.api_request('get', 'https://example.com').status_code == 429
    assert slept == []


def test_api_request_stops_when_the_total_wait_budget_is_spent(monkeypatch):
    slept = []
    monkeypatch.setattr(utils.time, 'sleep', lambda s: slept.append(s))
    monkeypatch.setattr(
        'requests.get', lambda url, **kw: _Response(429, headers={'Retry-After': '55'})
    )

    utils.api_request('get', 'https://example.com')

    assert slept == [55.0, 55.0]  # a third would pass the 120 s budget


def test_api_request_exits_1_on_a_connection_error_or_raises_when_asked(monkeypatch):
    import requests

    def refuse(url, **kwargs):
        raise requests.exceptions.ConnectionError('refused')

    monkeypatch.setattr('requests.get', refuse)

    with pytest.raises(typer.Exit):
        utils.api_request('get', 'https://example.com')
    with pytest.raises(utils.NetworkError, match='refused'):
        utils.api_request('get', 'https://example.com', exit_on_error=False)


@pytest.mark.parametrize(
    ('response', 'expected'),
    [
        (_Response(400, {'detail': 'destination: bad'}), '400: destination: bad'),
        (
            _Response(422, {'detail': [{'loc': ['body', 'payload', 'max_clicks'], 'msg': 'nope'}]}),
            '422: max_clicks: nope',
        ),
        (
            _Response(422, {'detail': [{'loc': ['query', 'limit'], 'msg': 'nope'}]}),
            '422: limit: nope',
        ),
        (_Response(500, text='<html>oops</html>'), '500: <html>oops</html>'),
        (_Response(401, {'detail': 'x'}), '401: x (run `login` again)'),
        (_Response(402, {'detail': 'x'}), '402: x (top up your credits on the website)'),
        (
            _Response(429, {'detail': 'x'}, headers={'Retry-After': '2400'}),
            '429: x (rate limited, try again in 40 minutes)',
        ),
    ],
)
def test_api_error_text(response, expected):
    assert utils.api_error_text(response) == expected


def test_api_error_text_without_hints_leaves_a_401_alone():
    assert utils.api_error_text(_Response(401, {'detail': 'x'}), hints=False) == '401: x'


def test_raise_for_api_error(capsys):
    utils.raise_for_api_error(_Response(200))
    with pytest.raises(typer.Exit):
        utils.raise_for_api_error(_Response(404, {'detail': 'Not Found'}))


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
    assert utils.format_wait(seconds) == text
