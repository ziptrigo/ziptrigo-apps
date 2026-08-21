"""
Environment file selection for the admin CLIs.

The implementation lives in the shared ``utils`` package so Django ``settings.py`` modules can
import it too. This module only binds it to this repository's layout: the repo root as the default
project root, and :class:`WebApp` as the way to name a web app.
"""

from pathlib import Path

from utils.environment import (
    IGNORED_ENV_FILE_SUFFIXES,
    SUPPORTED_ENVIRONMENTS,
    Environment,
    EnvSelection,
    env_from_file,
    file_from_env,
)
from utils.environment import select_env as _select_env

from . import PROJECT_ROOT
from .web_app import WebApp

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
    web_app: WebApp | str | None = None,
) -> EnvSelection:
    """Select the environment files for a web app, defaulting to this repository's root."""
    return _select_env(
        project_root,
        environment=environment,
        web_app=web_app.value if isinstance(web_app, WebApp) else web_app,
    )
