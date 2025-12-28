"""
Environment file selection.

This module is intentionally Django-free so it can be imported from ``config/settings.py``.

Naming convention:
- ``.env.<ENVIRONMENT>`` files at the project root (e.g. ``.env.dev``, ``.env.prod``)

Selection rules:
- If ``ENVIRONMENT`` env var is set, use that and load ``.env.<ENVIRONMENT>``.
- Otherwise, detect a single ``.env.*`` file (excluding ``.env.example``). Fail if none or more than one.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from common import PROJECT_ROOT

SUPPORTED_ENVIRONMENTS = ['dev', 'prod']
IGNORED_ENV_FILE_SUFFIXES = {'example'}


@dataclass(frozen=True, slots=True)
class EnvSelection:
    environment: str | None
    env_path: Path | None
    errors: list[str]
    warnings: list[str]


def env_from_file(file: Path) -> str | None:
    name = file.name
    if not name.startswith('.env.'):
        return None

    suffix = name.removeprefix('.env.')
    return suffix.lower() if suffix else None


def file_from_env(project_root: Path, env: str) -> Path:
    return project_root / f'.env.{env}'


def select_env(project_root: Path = PROJECT_ROOT, environment: str | None = None) -> EnvSelection:
    errors: list[str] = []
    warnings: list[str] = []

    environment: str = (environment or os.getenv('ENVIRONMENT', '')).lower().strip()  # type: ignore

    if environment:
        if environment not in SUPPORTED_ENVIRONMENTS:
            errors.append(
                f'Environment `{environment}` must be one of '
                f'{SUPPORTED_ENVIRONMENTS} (case insensitive).'
            )
            return EnvSelection(
                environment=environment, env_path=None, errors=errors, warnings=warnings
            )

        env_path = file_from_env(project_root, environment)
        if not env_path.exists():
            errors.append(f'Environment file `{env_path}` not found.')
            return EnvSelection(
                environment=environment, env_path=None, errors=errors, warnings=warnings
            )

        return EnvSelection(
            environment=environment,
            env_path=env_path,
            errors=errors,
            warnings=warnings,
        )

    files = sorted(project_root.glob('.env.*'))

    valid_files: list[Path] = []
    unknown_files: list[Path] = []

    for file in files:
        env = env_from_file(file)
        if not env:
            continue

        if env in IGNORED_ENV_FILE_SUFFIXES:
            continue

        if env in SUPPORTED_ENVIRONMENTS:
            valid_files.append(file)
        else:
            unknown_files.append(file)

    if unknown_files:
        warnings.append(
            'Unknown environment file(s) found in project root:\n'
            + '\n'.join(map(str, unknown_files))
        )

    if not valid_files:
        errors.append(f'No environment file found in `{project_root}` matching `.env.<env>`.')
        return EnvSelection(environment=None, env_path=None, errors=errors, warnings=warnings)

    if len(valid_files) > 1:
        errors.append(
            'More than one environment file found in project root:\n'
            + '\n'.join(map(str, valid_files))
        )
        return EnvSelection(environment=None, env_path=None, errors=errors, warnings=warnings)

    file = valid_files[0]
    env = env_from_file(file)
    if not env:
        errors.append(f'Could not parse environment from file `{file}`.')
        return EnvSelection(environment=None, env_path=None, errors=errors, warnings=warnings)

    return EnvSelection(environment=env, env_path=file, errors=errors, warnings=warnings)
