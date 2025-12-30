"""
Environment file selection.

This module is intentionally Django-free so it can be imported from ``settings.py``.

Naming convention:
- ``.env.<ENVIRONMENT>`` files at the project root (e.g. ``.env.dev``, ``.env.prod``)

Selection rules:
- If ``ENVIRONMENT`` env var is set, use that and load ``.env.<ENVIRONMENT>``.
- Otherwise, detect a single ``.env.*`` file (excluding ``.env.example``). Fail if none or more than one.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from common import PROJECT_ROOT
from common.web_app import WebApp

IGNORED_ENV_FILE_SUFFIXES = {'example'}


class Environment(Enum):
    DEV = 'dev'
    PROD = 'prod'


@dataclass(frozen=True, slots=True)
class EnvSelection:
    environment: Environment | None

    web_app: WebApp | None = None
    """
    The web app that the environment is for, if any.

    If the web app is not set, this class is used to represent the common environment and the
    environment file is the first position of ``all_env_paths``.
    """

    env_path: Path | None = None
    """The path to the environment file for the web app, if any."""

    errors: list[str] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)

    @property
    def all_env_paths(self) -> list[Path]:
        """
        Returns a list of environment file paths to be loaded, in the order they should be loaded.

        The common environment file is always loaded first, followed by the web app environment
        file.
        """
        if not self.environment:
            return []

        env_paths = [file_from_env(PROJECT_ROOT / 'common', self.environment)]
        if self.env_path:
            env_paths.append(self.env_path)
        return env_paths


def env_from_file(file: Path) -> Environment | None:
    name = file.name
    if not name.startswith('.env.'):
        return None

    suffix = name.removeprefix('.env.').lower()

    if suffix and suffix not in IGNORED_ENV_FILE_SUFFIXES:
        return Environment(suffix)
    return None


def file_from_env(project_root: Path, environment: Environment) -> Path:
    return project_root / f'.env.{environment.value}'


def select_env(
    project_root: Path = PROJECT_ROOT,
    environment: str | Environment | None = None,
    web_app: WebApp | None = None,
) -> EnvSelection:
    errors: list[str] = []
    warnings: list[str] = []

    environment = environment or os.getenv('ENVIRONMENT', '')

    # Environment is set, return that selection
    if environment:
        if isinstance(environment, str):
            try:
                env = Environment(environment.lower().strip())
            except ValueError:
                errors.append(
                    f'Environment `{environment}` must be one of '
                    f'{[x.value for x in Environment]} (case insensitive).'
                )
                return EnvSelection(
                    environment=None,
                    web_app=web_app,
                    errors=errors,
                    warnings=warnings,
                )
        else:
            env = environment

        if not web_app:
            return EnvSelection(environment=env, errors=errors, warnings=warnings)

        env_path = file_from_env(project_root / web_app.value, env)
        if not env_path.exists():
            errors.append(f'Environment file `{env_path}` not found.')
            return EnvSelection(environment=env, web_app=web_app, errors=errors, warnings=warnings)

        return EnvSelection(
            environment=env, web_app=web_app, env_path=env_path, errors=errors, warnings=warnings
        )

    # Environment was not set, check if there's only one environment file and return that selection
    files = sorted(project_root.glob('.env.*'))

    valid_files: list[Path] = []
    unknown_files: list[Path] = []

    for file in files:
        try:
            env = env_from_file(file)
            if not env:
                continue
            valid_files.append(file)
        except ValueError:
            unknown_files.append(file)
            continue

    if unknown_files:
        warnings.append(
            'Unknown environment file(s) found in project root:\n'
            + '\n'.join(map(str, unknown_files))
        )

    if not valid_files:
        errors.append(f'No environment file found in `{project_root}` matching `.env.<env>`.')
        return EnvSelection(environment=None, web_app=web_app, errors=errors, warnings=warnings)

    if len(valid_files) > 1:
        errors.append(
            'More than one environment file found in project root:\n'
            + '\n'.join(map(str, valid_files))
        )
        return EnvSelection(environment=None, web_app=web_app, errors=errors, warnings=warnings)

    file = valid_files[0]
    env = env_from_file(file)
    if not env:
        errors.append(f'Could not parse environment from file `{file}`.')
        return EnvSelection(environment=None, web_app=web_app, errors=errors, warnings=warnings)

    return EnvSelection(
        environment=env, web_app=web_app, env_path=file, errors=errors, warnings=warnings
    )
