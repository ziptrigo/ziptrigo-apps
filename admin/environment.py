"""
Environment file selection for the admin CLIs.

The implementation lives in `config/environment.py` so Django's `settings.py` can import it too.
This module only binds it to this repository's layout: the repo root as the default project root.
"""

from pathlib import Path

from config.environment import (
    IGNORED_ENV_FILE_SUFFIXES,
    SUPPORTED_ENVIRONMENTS,
    Environment,
    EnvSelection,
    env_from_file,
    file_from_env,
)
from config.environment import select_env as _select_env

from . import PROJECT_ROOT

__all__ = [
    'IGNORED_ENV_FILE_SUFFIXES',
    'SUPPORTED_ENVIRONMENTS',
    'EnvSelection',
    'Environment',
    'env_from_file',
    'file_from_env',
    'select_env',
]


def select_env(
    project_root: Path = PROJECT_ROOT,
    environment: str | Environment | None = None,
) -> EnvSelection:
    """Select the environment file, defaulting to this repository's root."""
    return _select_env(project_root, environment=environment)
