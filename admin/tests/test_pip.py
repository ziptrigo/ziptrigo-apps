"""Unit tests for `admin/pip.py`'s sync command building."""

from admin import pip


def _command(requirements, **kwargs) -> list[str]:
    return pip._sync_command(requirements, **kwargs)


def test_sync_main_excludes_dev_group():
    command = _command(['main'])
    assert '--no-dev' in command
    assert '--group' not in command
    assert '--all-groups' not in command


def test_sync_dev_includes_dev_group():
    command = _command(['dev'])
    assert command[command.index('--group') + 1] == 'dev'
    assert '--no-dev' not in command


def test_sync_main_and_dev_includes_dev_group():
    command = _command(['main', 'dev'])
    assert 'dev' in command
    assert '--no-dev' not in command


def test_sync_without_scopes_installs_all_groups():
    for requirements in (None, []):
        command = _command(requirements)
        assert '--all-groups' in command
        assert '--no-dev' not in command


def test_install_main_is_inexact_and_excludes_dev():
    command = _command(['main'], inexact=True)
    assert '--inexact' in command
    assert '--no-dev' in command
