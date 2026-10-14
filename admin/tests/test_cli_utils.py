"""Unit tests for the helpers the JWT HTTP-client CLIs (`qrcode`, `filetransfer`, `receipts`)
share in `admin/utils.py`: the site URL, one token file per CLI and environment, the password
prompt. Plain file-system and environment logic: no Django, no database, no network."""

import pytest
import typer

from admin import utils

pytestmark = pytest.mark.unit

CLIS = ['qrcode', 'filetransfer', 'receipts']


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
