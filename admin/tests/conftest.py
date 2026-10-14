"""Fixtures shared by the CLI tests: no Django, no database, no network, no real `.env.*` file and
no real home directory."""

import pytest

from admin import utils


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    """Every command calls `select_environment(environment)` first, which reads a real `.env.*`
    file off disk -- irrelevant to what these tests check, and CI may not have one at all. It also
    remembers the selected environment, so start every test on `local`."""
    monkeypatch.setattr(
        utils, 'set_environment', lambda environment=None, *args, **kwargs: environment
    )
    monkeypatch.setattr(utils, '_environment', 'local')


@pytest.fixture(autouse=True)
def _site_url(monkeypatch):
    """The site URL comes from the environment (`utils.site_url`); start every test without one."""
    monkeypatch.delenv('API_BASE_URL', raising=False)
    monkeypatch.delenv('BASE_URL', raising=False)


@pytest.fixture(autouse=True)
def token_dir(tmp_path, monkeypatch):
    """Where the CLIs keep their token files, instead of the real home directory."""
    monkeypatch.setattr(utils, 'TOKEN_DIR', tmp_path)
    return tmp_path


@pytest.fixture
def loads_base_url(monkeypatch):
    """The environment file sets `BASE_URL` (as `.env.prod` does) at the moment a command selects
    its environment, i.e. after the CLI module has been imported."""

    def set_environment(environment=None, *args, **kwargs):
        monkeypatch.setenv('BASE_URL', 'https://prod.example')
        return environment

    monkeypatch.setattr(utils, 'set_environment', set_environment)
