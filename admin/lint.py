#!python
"""
Linting and static type checking.
"""

from typing import Annotated

import typer

from . import PROJECT_ROOT
from .utils import DryAnnotation, logger, run
from .web_app import WebApp

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

# The Django app package `ty` needs to type-check, keyed by `WebApp`. This differs from
# `WebApp.value` for `user-service`, whose app package is `users`, not `user-service` -- see
# `WebApp`'s docstring for why the directory name and the app name aren't always the same thing.
# Every `WebApp` member must have an entry here -- indexed via `_APP_PACKAGE[web_app]` below rather
# than `.items()`, so a member nobody added to this map raises `KeyError` instead of silently never
# being type-checked.
_APP_PACKAGE: dict[WebApp, str] = {
    WebApp.QR_CODE: 'qr_code',
    WebApp.USERS: 'users',
}

_ADMIN_TARGET = 'admin'

# Both shared packages are `sys.path`-inserted at runtime (see `admin/__init__.py` and
# `config/settings.py` in each service) rather than installed into the environment, so `ty` needs
# to be told about them explicitly via `--extra-search-path`.
_SHARED_PACKAGE_PATHS = [
    PROJECT_ROOT / 'shared' / 'utils',
    PROJECT_ROOT / 'shared' / 'auth_client',
]


@app.command(name='ruff')
def lint_ruff(
    path: Annotated[str, typer.Argument(help='Path to directory or file to lint.')] = '.',
    check: Annotated[
        bool,
        typer.Option(
            help='Check-only mode: report violations without fixing or reformatting. '
            'Exits non-zero if any issues are found. Use this in CI.',
        ),
    ] = False,
    dry: DryAnnotation = False,
):
    if check:
        run('ruff', 'check', path, dry=dry)
        run('ruff', 'format', '--check', path, dry=dry)
    else:
        run('ruff', 'check', '--fix', path, dry=dry)
        run('ruff', 'format', path, dry=dry)


@app.command(name='ty')
def lint_ty(
    target: Annotated[
        str | None,
        typer.Argument(
            help='Limit the check to one target: a web app directory name '
            f'({", ".join(w.value for w in WebApp)}) or `admin`. Defaults to running every '
            'target.',
            show_default=False,
        ),
    ] = None,
    dry: DryAnnotation = False,
):
    """
    Type-check with `ty`, Astral's type checker.

    `ty` runs once per web app plus once for `admin/`, each with its own `cwd`, for the same
    reason `admin/test.py:_test_env` runs pytest per service rather than once from the repo root:
    both services name their settings package `config`, so a single repo-root invocation can't
    disambiguate which `config` a relative import belongs to. Unlike mypy+django-stubs, `ty`
    doesn't construct a Django-aware plugin, so there's no per-target crash -- just per-target
    search paths (`--extra-search-path`) so first-party and shared-package imports resolve.

    Each web app target checks both its app package (`_APP_PACKAGE`) and its `config` package --
    the latter is where the env-selection logic that raises on failure lives. `shared/utils/`,
    `shared/auth_client/`, `tests/` (both services) and `tests_e2e/` are still not checked; they're
    search paths only.

    TODO(#45): this currently surfaces a real, untriaged backlog of type errors -- see the issue
    for counts. A good chunk of it is Django model/queryset attribute-inference that
    mypy+django-stubs used to catch via a semantic-analysis plugin; `ty` has no equivalent plugin
    yet, so it reports those as unresolved attributes on the generic Django base classes instead of
    on the project's actual models. Triaging the backlog is separate follow-up work tracked in
    #45, not this change. `--exit-zero` keeps this step non-blocking in `inv lint all` until that
    triage lands -- remove it once the backlog is clear so `ty` actually gates the build.
    """
    shared_search_path_args = []
    for shared_package_path in _SHARED_PACKAGE_PATHS:
        shared_search_path_args.extend(['--extra-search-path', str(shared_package_path)])

    web_apps: list[WebApp] = []
    run_admin = False

    if target is None:
        web_apps = list(WebApp)
        run_admin = True
    elif target == _ADMIN_TARGET:
        run_admin = True
    else:
        try:
            web_apps = [WebApp(target)]
        except ValueError:
            valid = ', '.join([*(w.value for w in WebApp), _ADMIN_TARGET])
            raise typer.BadParameter(f'Unknown target {target!r}; expected one of: {valid}.')

    for web_app in web_apps:
        run(
            'ty',
            'check',
            '--exit-zero',
            '--extra-search-path',
            '.',
            *shared_search_path_args,
            _APP_PACKAGE[web_app],
            'config',
            dry=dry,
            cwd=PROJECT_ROOT / web_app.value,
        )

    if run_admin:
        run(
            'ty',
            'check',
            '--exit-zero',
            *shared_search_path_args,
            'admin',
            dry=dry,
            cwd=PROJECT_ROOT,
        )


@app.command(name='all')
def lint_all(
    check: Annotated[
        bool,
        typer.Option(
            help='Check-only mode: report violations without fixing or reformatting. '
            'Exits non-zero if any issues are found. Use this in CI.',
        ),
    ] = False,
    dry: DryAnnotation = False,
):
    """
    Run all linters.

    Config for each of the tools is in `pyproject.toml`.
    """
    lint_ruff(check=check, dry=dry)
    lint_ty(dry=dry)

    logger.info('Done')


if __name__ == '__main__':
    app()
