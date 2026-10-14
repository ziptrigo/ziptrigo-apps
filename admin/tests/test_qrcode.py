"""Unit tests for `admin/qrcode.py`'s command wiring and for `admin/utils.write_private_file`. No
Django, no database, no network (`requests` is mocked); the shared token/site helpers are tested
in `test_cli_utils.py`."""

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
