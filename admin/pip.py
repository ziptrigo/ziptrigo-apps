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


class App(StrEnum):
    """App-specific dependency groups."""

    USERS = 'users'
    QR_CODE = 'qr_code'


class Requirements(StrEnum):
    """Dependency scopes."""

    MAIN = 'main'
    DEV = 'dev'


RequirementsAnnotation = Annotated[
    list[str] | None,
    typer.Argument(
        help='Dependency scope(s) to operate on. If not set, all scopes are used.\nValues can be '
        + ', '.join([f'`{x.value}`' for x in Requirements])
        + '.',
        show_default=False,
    ),
]

AppAnnotation = Annotated[
    list[str] | None,
    typer.Option(
        '--app',
        '-a',
        help='App-specific dependency group(s) to operate on.\nValues can be '
        + ', '.join([f'`{x.value}`' for x in App])
        + '.',
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
    try:
        return Requirements[normalized.upper()]
    except KeyError:
        try:
            return Requirements(normalized)
        except ValueError:
            logger.error(f'`{requirement}` is an unknown dependency scope.')
            raise typer.Exit(1)


def _normalize_app(app_name: str | App) -> App:
    if isinstance(app_name, App):
        return app_name

    normalized = app_name.strip().lower()
    try:
        return App[normalized.upper()]
    except KeyError:
        try:
            return App(normalized)
        except ValueError:
            logger.error(f'`{app_name}` is an unknown app.')
            raise typer.Exit(1)


def _get_apps(apps: list[str | App] | None) -> list[App]:
    if apps is None:
        return []

    unique_apps: list[App] = []
    for app_name in apps:
        normalized = _normalize_app(app_name)
        if normalized not in unique_apps:
            unique_apps.append(normalized)
    return unique_apps


def _get_requirements(requirements: list[str | Requirements] | None) -> list[Requirements]:
    if requirements is None or len(requirements) == 0:
        return []

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


def _selected_groups(
    requirements: list[str | Requirements] | None, apps: list[str | App] | None
) -> list[str]:
    selected_reqs = set(_get_requirements(requirements))
    selected_apps = set(_get_apps(apps))
    groups: list[str] = []

    if Requirements.DEV in selected_reqs:
        groups.append(Requirements.DEV.value)

    for app_name in selected_apps:
        groups.append(app_name.value)

    return groups


def _sync_command(
    requirements: list[str | Requirements] | None,
    apps: list[str | App] | None = None,
    *,
    inexact: bool = False,
) -> list[str]:
    args = ['uv', 'sync', '--frozen', '--no-install-project']
    if _should_use_active_env():
        args.append('--active')
    if inexact:
        args.append('--inexact')

    selected_reqs = _get_requirements(requirements)
    selected_apps = _get_apps(apps)

    if (requirements is None or len(requirements) == 0) and (apps is None or len(apps) == 0):
        args.append('--all-groups')
        return args

    for group in _selected_groups(requirements, apps):
        args.extend(['--group', group])

    return args


def _canonical_package_name(package: str) -> str:
    return re.sub(r'[-_.]+', '-', package).lower()


def _extract_requirement_name(requirement: str) -> str:
    match = re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*', requirement.strip())
    if not match:
        raise ValueError(f'Invalid dependency declaration: {requirement}')
    return _canonical_package_name(match.group())


def _load_declared_packages() -> dict[Requirements | App, set[str]]:
    config = tomllib.loads(PYPROJECT_FILE.read_text(encoding='utf-8'))
    project = config.get('project', {})
    dependency_groups = config.get('dependency-groups', {})

    main = {_extract_requirement_name(dep) for dep in project.get('dependencies', [])}
    qr_code = {_extract_requirement_name(dep) for dep in dependency_groups.get('qr_code', [])}
    users = {_extract_requirement_name(dep) for dep in dependency_groups.get('users', [])}
    dev = {_extract_requirement_name(dep) for dep in dependency_groups.get('dev', [])}
    return {
        Requirements.MAIN: main,
        App.USERS: main | users,
        App.QR_CODE: main | qr_code,
        Requirements.DEV: main | dev,
    }


def _get_declared_packages_for_requirements(
    requirements: list[str | Requirements] | None, apps: list[str | App] | None
) -> set[str]:
    declared_packages = _load_declared_packages()
    selected_reqs = _get_requirements(requirements)
    selected_apps = _get_apps(apps)
    packages: set[str] = set()
    for requirement in selected_reqs:
        packages |= declared_packages[requirement]
    for app_name in selected_apps:
        packages |= declared_packages[app_name]
    return packages


@app.command(name='compile')
def pip_compile(
    requirements: RequirementsAnnotation = None,
    apps: AppAnnotation = None,
    clean: Annotated[
        bool,
        typer.Option(help='Delete the existing `uv.lock` file, forcing a clean lock refresh.'),
    ] = False,
    dry: DryAnnotation = False,
):
    """Refresh `uv.lock`."""
    if clean and not dry:
        UV_LOCK_FILE.unlink(missing_ok=True)

    # Note: uv.lock is always shared, we don't filter it by apps/requirements during lock
    run('uv', 'lock', dry=dry)


@app.command(name='sync')
def pip_sync(
    requirements: RequirementsAnnotation = None, apps: AppAnnotation = None, dry: DryAnnotation = False
):
    """Synchronize the environment with `uv.lock`."""
    run(*_sync_command(requirements, apps), dry=dry, env=_uv_env())


@app.command(name='package')
def pip_package(
    requirements: RequirementsAnnotation = None,
    apps: AppAnnotation = None,
    package: Annotated[
        list[str], typer.Option('--package', '-p', help='One or more packages to upgrade.')
    ] = None,
    dry: DryAnnotation = False,
):
    """Upgrade one or more packages."""
    if package is None:
        logger.error('No packages specified to upgrade.')
        raise typer.Exit(1)

    declared_packages = _get_declared_packages_for_requirements(requirements, apps)
    unknown_packages = [
        item for item in package if _canonical_package_name(item) not in declared_packages
    ]
    if unknown_packages:
        logger.error(
            'Package(s) not declared in the selected dependency scope(s): '
            + ', '.join(unknown_packages)
        )
        raise typer.Exit(1)

    run('uv', 'lock', *multiple_parameters('--upgrade-package', *package), dry=dry)


@app.command(name='upgrade')
def pip_upgrade(
    requirements: RequirementsAnnotation = None, apps: AppAnnotation = None, dry: DryAnnotation = False
):
    """
    Try to upgrade all dependencies to their latest versions.
    """
    run('uv', 'lock', '--upgrade', dry=dry)


@app.command(name='install')
def pip_install(
    requirements: RequirementsAnnotation = None, apps: AppAnnotation = None, dry: DryAnnotation = False
):
    """Install dependencies from `uv.lock` without removing unrelated packages."""
    run(*_sync_command(requirements, apps, inexact=True), dry=dry, env=_uv_env())


if __name__ == '__main__':
    app()
