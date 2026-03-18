#!python
"""
Python packages related tasks.
"""

import os
import re
import tomllib
from enum import StrEnum
from typing import Annotated

import typer

from admin import PROJECT_ROOT
from admin.utils import DryAnnotation, logger, multiple_parameters, run

PYPROJECT_FILE = PROJECT_ROOT / 'pyproject.toml'
UV_LOCK_FILE = PROJECT_ROOT / 'uv.lock'
DEFAULT_VIRTUAL_ENV = PROJECT_ROOT / '.venv313'

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)


class Requirements(StrEnum):
    """Dependency scopes."""

    MAIN = 'main'
    QR_CODE = 'qr_code'
    USERS = 'users'
    DEV = 'dev'


LEGACY_REQUIREMENT_ALIASES = {
    'requirements-app-qr_code': Requirements.QR_CODE,
    'requirements-app-users': Requirements.USERS,
    'requirements-base': Requirements.MAIN,
    'requirements-dev': Requirements.DEV,
}

RequirementsAnnotation = Annotated[
    list[str] | None,
    typer.Argument(
        help='Dependency scope(s) to operate on. If not set, all scopes are used.\nValues can be '
        + ', '.join([f'`{x.name.lower()}`' for x in Requirements])
        + '. Legacy aliases `requirements-base`, `requirements-app-users`, '
        + '`requirements-app-qr_code`, and `requirements-dev` are also accepted.',
        show_default=False,
    ),
]


def _uv_env() -> dict[str, str]:
    env = os.environ.copy()

    if env.get('VIRTUAL_ENV'):
        return env

    if DEFAULT_VIRTUAL_ENV.exists():
        env['VIRTUAL_ENV'] = str(DEFAULT_VIRTUAL_ENV)

    return env


def _should_use_active_env() -> bool:
    env = _uv_env()
    return bool(env.get('VIRTUAL_ENV'))


def _normalize_requirement(requirement: str | Requirements) -> Requirements:
    if isinstance(requirement, Requirements):
        return requirement

    normalized = requirement.strip().lower()
    if normalized in LEGACY_REQUIREMENT_ALIASES:
        return LEGACY_REQUIREMENT_ALIASES[normalized]

    try:
        return Requirements[normalized.upper()]
    except KeyError:
        try:
            return Requirements(normalized)
        except ValueError:
            logger.error(f'`{requirement}` is an unknown dependency scope.')
            raise typer.Exit(1)


def _get_requirements(requirements: list[str | Requirements] | None) -> list[Requirements]:
    if requirements is None:
        return list(Requirements)

    unique_requirements: list[Requirements] = []
    for requirement in requirements:
        normalized = _normalize_requirement(requirement)
        if normalized not in unique_requirements:
            unique_requirements.append(normalized)
    return unique_requirements


def _scopes_label(requirements: list[str | Requirements] | None) -> str:
    return ', '.join(requirement.value for requirement in _get_requirements(requirements))


def _log_shared_lockfile(requirements: list[str | Requirements] | None, action: str):
    selected = set(_get_requirements(requirements))
    if selected != set(Requirements):
        logger.info(
            f'`uv.lock` is shared across all dependency scopes; {action} still refreshes the '
            f'shared lockfile (requested: {_scopes_label(requirements)}).'
        )


def _selected_groups(requirements: list[str | Requirements] | None) -> list[str]:
    selected = set(_get_requirements(requirements))
    groups: list[str] = []

    if Requirements.DEV in selected:
        groups.append(Requirements.DEV.value)
    elif Requirements.QR_CODE in selected:
        groups.append(Requirements.QR_CODE.value)

    return groups


def _sync_command(
    requirements: list[str | Requirements] | None,
    *,
    inexact: bool = False,
) -> list[str]:
    args = ['uv', 'sync', '--frozen', '--no-install-project']
    if _should_use_active_env():
        args.append('--active')
    if inexact:
        args.append('--inexact')

    selected = _get_requirements(requirements)
    if requirements is None or set(selected) == set(Requirements):
        args.append('--all-groups')
        return args

    for group in _selected_groups(requirements):
        args.extend(['--group', group])

    return args


def _canonical_package_name(package: str) -> str:
    return re.sub(r'[-_.]+', '-', package).lower()


def _extract_requirement_name(requirement: str) -> str:
    match = re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*', requirement.strip())
    if not match:
        raise ValueError(f'Invalid dependency declaration: {requirement}')
    return _canonical_package_name(match.group())


def _load_declared_packages() -> dict[Requirements, set[str]]:
    config = tomllib.loads(PYPROJECT_FILE.read_text(encoding='utf-8'))
    project = config.get('project', {})
    dependency_groups = config.get('dependency-groups', {})

    main = {_extract_requirement_name(dep) for dep in project.get('dependencies', [])}
    qr_code = {_extract_requirement_name(dep) for dep in dependency_groups.get('qr_code', [])}
    dev = {_extract_requirement_name(dep) for dep in dependency_groups.get('dev', [])}
    return {
        Requirements.MAIN: main,
        Requirements.USERS: main,
        Requirements.QR_CODE: main | qr_code,
        Requirements.DEV: main | dev,
    }


def _get_declared_packages_for_requirements(
    requirements: list[str | Requirements] | None,
) -> set[str]:
    declared_packages = _load_declared_packages()
    selected = _get_requirements(requirements)
    packages: set[str] = set()
    for requirement in selected:
        packages |= declared_packages[requirement]
    return packages


@app.command(name='compile')
def pip_compile(
    requirements: RequirementsAnnotation = None,
    clean: Annotated[
        bool,
        typer.Option(help='Delete the existing `uv.lock` file, forcing a clean lock refresh.'),
    ] = False,
    dry: DryAnnotation = False,
):
    """Refresh `uv.lock`."""
    if clean and not dry:
        UV_LOCK_FILE.unlink(missing_ok=True)

    _log_shared_lockfile(requirements, 'compiling')
    run('uv', 'lock', dry=dry)


@app.command(name='sync')
def pip_sync(requirements: RequirementsAnnotation = None, dry: DryAnnotation = False):
    """Synchronize the environment with `uv.lock`."""
    run(*_sync_command(requirements), dry=dry, env=_uv_env())


@app.command(name='package')
def pip_package(
    requirements: RequirementsAnnotation,
    package: Annotated[
        list[str], typer.Option('--package', '-p', help='One or more packages to upgrade.')
    ],
    dry: DryAnnotation = False,
):
    """Upgrade one or more packages."""
    declared_packages = _get_declared_packages_for_requirements(requirements)
    unknown_packages = [
        item for item in package if _canonical_package_name(item) not in declared_packages
    ]
    if unknown_packages:
        logger.error(
            'Package(s) not declared in the selected dependency scope(s): '
            + ', '.join(unknown_packages)
        )
        raise typer.Exit(1)

    _log_shared_lockfile(requirements, 'upgrading packages')
    run('uv', 'lock', *multiple_parameters('--upgrade-package', *package), dry=dry)


@app.command(name='upgrade')
def pip_upgrade(requirements: RequirementsAnnotation = None, dry: DryAnnotation = False):
    """
    Try to upgrade all dependencies to their latest versions.

    Equivalent to `compile` with `--clean`.
    """
    _log_shared_lockfile(requirements, 'upgrading')
    run('uv', 'lock', '--upgrade', dry=dry)


@app.command(name='install')
def pip_install(requirements: RequirementsAnnotation = None, dry: DryAnnotation = False):
    """Install dependencies from `uv.lock` without removing unrelated packages."""
    run(*_sync_command(requirements, inexact=True), dry=dry, env=_uv_env())


if __name__ == '__main__':
    app()
