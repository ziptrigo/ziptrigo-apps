"""
Environment file selection.

This module is intentionally Django-free so it can be imported from ``settings.py``, and
dependency-free so it can be imported before the environment is set up.

Naming convention:
- ``.env.<ENVIRONMENT>`` files (e.g. ``.env.dev``, ``.env.prod``)

Selection rules:
- If ``ENVIRONMENT`` env var is set, use that and load ``.env.<ENVIRONMENT>``.
- Otherwise, detect a single ``.env.*`` file (excluding ``.env.example``). Fail if none or more
  than one.
"""

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# `.env.<suffix>` files that are never an environment of their own: the template, and the staging
# deployment's file (staging runs as `prod` on the server, with this file mounted as `.env.prod`).
IGNORED_ENV_FILE_SUFFIXES = {'example', 'staging'}


class Environment(StrEnum):
    """
    Environments this project can run in.

    A ``StrEnum`` so members compare equal to their value, which keeps callers that treat the
    environment as a plain string (``os.environ`` updates, f-strings) working unchanged.
    """

    DEV = 'dev'
    PROD = 'prod'


SUPPORTED_ENVIRONMENTS: list[str] = [x.value for x in Environment]


@dataclass(frozen=True, slots=True)
class EnvSelection:
    environment: Environment | None

    env_path: Path | None = None
    """The path to the selected environment file, if any."""

    errors: list[str] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)


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
    project_root: Path,
    environment: str | Environment | None = None,
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
                    f'{SUPPORTED_ENVIRONMENTS} (case insensitive).'
                )
                return EnvSelection(environment=None, errors=errors, warnings=warnings)
        else:
            env = environment

        env_path = file_from_env(project_root, env)
        if not env_path.exists():
            errors.append(f'Environment file `{env_path}` not found.')
            return EnvSelection(environment=env, errors=errors, warnings=warnings)

        return EnvSelection(environment=env, env_path=env_path, errors=errors, warnings=warnings)

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
        return EnvSelection(environment=None, errors=errors, warnings=warnings)

    if len(valid_files) > 1:
        errors.append(
            'More than one environment file found in project root:\n'
            + '\n'.join(map(str, valid_files))
        )
        return EnvSelection(environment=None, errors=errors, warnings=warnings)

    file = valid_files[0]
    env = env_from_file(file)
    if not env:
        errors.append(f'Could not parse environment from file `{file}`.')
        return EnvSelection(environment=None, errors=errors, warnings=warnings)

    return EnvSelection(environment=env, env_path=file, errors=errors, warnings=warnings)
