"""Unit tests for the parts of `admin/qrcode.py` and `admin/utils.py` that handle the CLIs' token
files. Plain file-system logic: no Django, no database, no network."""

import pytest

from admin import qrcode, utils

pytestmark = pytest.mark.unit


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / 'token'
    monkeypatch.setattr(qrcode, 'TOKEN_FILE', path)
    return path


def test_save_token_creates_file_with_owner_only_permissions(token_file):
    qrcode.save_token('secret')

    assert token_file.read_text() == 'secret'
    assert (token_file.stat().st_mode & 0o777) == 0o600
    assert qrcode.get_token() == 'secret'


def test_save_token_tightens_a_file_that_already_exists_with_looser_permissions(token_file):
    token_file.write_text('old')
    token_file.chmod(0o644)

    qrcode.save_token('secret')

    assert token_file.read_text() == 'secret'
    assert (token_file.stat().st_mode & 0o777) == 0o600


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
