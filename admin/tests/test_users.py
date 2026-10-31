"""Unit tests for `admin/users.py` (`inv users create|confirm`): how the commands are assembled.
Like the other CLI tests this module never touches Django or a database: `run` is mocked, and the
management commands themselves are tested in `apps/accounts/tests/test_user_commands.py`.
"""

import sys

import pytest
from typer.testing import CliRunner

from admin import users

pytestmark = pytest.mark.unit

runner = CliRunner()


@pytest.fixture
def run(monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append((list(args), kwargs))

    monkeypatch.setattr(users, 'run', fake)
    monkeypatch.delenv('USERS_PASSWORD', raising=False)
    return calls


def invoke(*args, **kwargs):
    return runner.invoke(users.app, list(args), **kwargs)


class TestCreate:
    def test_runs_the_management_command_in_the_chosen_environment(self, run):
        result = invoke('create', 'local', 'me@example.com', 'Sup3r-secret')

        assert result.exit_code == 0, result.output
        args, kwargs = run[0]
        assert args == [sys.executable, 'manage.py', 'create_user', 'me@example.com']
        assert kwargs['env']['ENVIRONMENT'] == 'local'
        assert kwargs['cwd'] == users.PROJECT_ROOT

    def test_the_password_never_appears_in_the_command_line(self, run):
        invoke('create', 'local', 'me@example.com', 'Sup3r-secret', '--superuser', '--confirm')

        args, kwargs = run[0]
        assert 'Sup3r-secret' not in ' '.join(args)
        assert kwargs['env']['USERS_PASSWORD'] == 'Sup3r-secret'

    @pytest.mark.parametrize(
        ('flags', 'expected'),
        [
            ([], []),
            (['--superuser'], ['--superuser']),
            (['--staff'], ['--staff']),
            (['--confirm'], ['--confirm']),
            (['--superuser', '--confirm'], ['--superuser', '--confirm']),
            (['--staff', '--confirm'], ['--staff', '--confirm']),
        ],
    )
    def test_the_flags_are_passed_on(self, run, flags, expected):
        invoke('create', 'local', 'me@example.com', 'Sup3r-secret', *flags)

        assert run[0][0][4:] == expected

    def test_superuser_and_staff_are_mutually_exclusive(self, run):
        result = invoke(
            'create', 'local', 'me@example.com', 'Sup3r-secret', '--superuser', '--staff'
        )

        assert result.exit_code != 0
        assert 'cannot be used together' in result.output
        assert run == []

    def test_prod_is_accepted_and_other_environments_are_not(self, run):
        assert invoke('create', 'prod', 'me@example.com', 'Sup3r-secret').exit_code == 0
        assert run[0][1]['env']['ENVIRONMENT'] == 'prod'
        assert invoke('create', 'staging', 'me@example.com', 'Sup3r-secret').exit_code != 0

    def test_the_password_can_come_from_the_environment(self, run, monkeypatch):
        monkeypatch.setenv('USERS_PASSWORD', 'From-env1')

        result = invoke('create', 'local', 'me@example.com')

        assert result.exit_code == 0, result.output
        assert run[0][1]['env']['USERS_PASSWORD'] == 'From-env1'

    def test_without_a_password_it_is_asked_for_with_the_echo_hidden(self, run):
        result = invoke('create', 'local', 'me@example.com', input='Typed-pass1\n')

        assert result.exit_code == 0, result.output
        assert run[0][1]['env']['USERS_PASSWORD'] == 'Typed-pass1'
        assert 'Typed-pass1' not in result.output

    def test_dry_runs_nothing_that_matters(self, run):
        invoke('create', 'local', 'me@example.com', 'Sup3r-secret', '--dry')

        assert run[0][1]['dry'] is True


class TestConfirm:
    def test_runs_confirm_email(self, run):
        result = invoke('confirm', 'local', 'me@example.com')

        assert result.exit_code == 0, result.output
        args, kwargs = run[0]
        assert args == [sys.executable, 'manage.py', 'confirm_email', 'me@example.com']
        assert kwargs['env']['ENVIRONMENT'] == 'local'
        assert 'USERS_PASSWORD' not in kwargs['env']

    def test_dry(self, run):
        invoke('confirm', 'local', 'me@example.com', '--dry')

        assert run[0][1]['dry'] is True


def test_the_module_is_registered_with_inv():
    import tomllib
    from pathlib import Path

    config = tomllib.loads((Path(users.__file__).parents[1] / 'pyproject.toml').read_text())
    assert 'admin.users' in config['tool']['typer-invoke']['modules']
