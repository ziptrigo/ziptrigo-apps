"""Unit tests for environment file selection.

`src/qr_code/common/environment.py` must remain Django-free so it can be imported from `config/settings.py`.
"""

import os

from src.qr_code.common.environment import SUPPORTED_ENVIRONMENTS, select_env


class TestSelectEnv:
    def test_env_from_env_var_dev(self, tmp_path) -> None:
        (tmp_path / '.env.dev').touch()
        selection = select_env(tmp_path, environment='dev')
        assert selection.environment == 'dev'
        assert selection.env_path == tmp_path / '.env.dev'
        assert selection.errors == []

    def test_env_from_env_var_case_insensitive(self, tmp_path) -> None:
        (tmp_path / '.env.dev').touch()
        selection = select_env(tmp_path, environment='DEV')
        assert selection.environment == 'dev'
        assert selection.env_path == tmp_path / '.env.dev'
        assert selection.errors == []

    def test_env_from_env_var_missing_file_is_error(self, tmp_path) -> None:
        selection = select_env(tmp_path, environment='dev')
        assert selection.environment == 'dev'
        assert selection.env_path is None
        assert any('not found' in e.lower() for e in selection.errors)

    def test_invalid_environment_is_error(self, tmp_path) -> None:
        selection = select_env(tmp_path, environment='invalid')
        assert selection.environment == 'invalid'
        assert selection.env_path is None
        assert any('must be one of' in e.lower() for e in selection.errors)

    def test_no_env_var_single_env_file_is_selected(self, tmp_path) -> None:
        (tmp_path / '.env.dev').touch()
        selection = select_env(tmp_path, environment='')
        assert selection.environment == 'dev'
        assert selection.env_path == tmp_path / '.env.dev'
        assert selection.errors == []

    def test_no_env_var_multiple_supported_env_files_is_error(self, tmp_path) -> None:
        (tmp_path / '.env.dev').touch()
        (tmp_path / '.env.prod').touch()
        selection = select_env(tmp_path, environment='')
        assert selection.environment is None
        assert selection.env_path is None
        assert any('more than one' in e.lower() for e in selection.errors)

    def test_unknown_env_files_are_warning(self, tmp_path) -> None:
        (tmp_path / '.env.unknown').touch()
        selection = select_env(tmp_path, environment='')
        assert selection.environment is None
        assert selection.env_path is None
        assert selection.errors
        assert selection.warnings

    def test_env_example_is_ignored(self, tmp_path) -> None:
        (tmp_path / '.env.example').touch()
        selection = select_env(tmp_path, environment='')
        assert selection.environment is None
        assert selection.env_path is None
        assert selection.errors
        assert selection.warnings == []

    def test_defaults_to_os_environ_when_environment_not_provided(self, tmp_path) -> None:
        (tmp_path / '.env.dev').touch()
        os.environ['ENVIRONMENT'] = 'dev'
        try:
            selection = select_env(tmp_path)
            assert selection.environment == 'dev'
            assert selection.env_path == tmp_path / '.env.dev'
        finally:
            os.environ.pop('ENVIRONMENT', None)


class TestEnvironmentConstants:
    def test_supported_environments_contains_dev_and_prod(self) -> None:
        assert 'dev' in SUPPORTED_ENVIRONMENTS
        assert 'prod' in SUPPORTED_ENVIRONMENTS

    def test_supported_environments_is_list(self) -> None:
        assert isinstance(SUPPORTED_ENVIRONMENTS, list)
