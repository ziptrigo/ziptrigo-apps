#!python
"""
Linting and static type checking.
"""

from typing import Annotated

import typer

from . import PROJECT_ROOT
from .django_app import DjangoApp
from .utils import DryAnnotation, logger, run

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

_ADMIN_TARGET = 'admin'


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
            help='Limit the check to one target: a Django app under `apps/` '
            f'({", ".join(a.value for a in DjangoApp)}) or `admin`. Defaults to checking '
            'everything.',
            show_default=False,
        ),
    ] = None,
    dry: DryAnnotation = False,
):
    """
    Type-check with `ty`, Astral's type checker.

    Runs once from the repo root over `apps/`, `config/`, `admin/` and the root-level
    `supervise.py` / `gunicorn.conf.py`. The apps' `tests/` packages
    are included; `tests_e2e/` is not.

    The diagnostic backlog this surfaced when `ty` replaced mypy (see #44) was triaged in #45: real
    issues were fixed, and the rest -- mostly Django model/queryset attribute-inference that
    mypy+django-stubs used to catch via a semantic-analysis plugin `ty` has no equivalent of yet --
    were handled at the point of declaration with an explicit annotation or `cast(...)`, or --
    where that's not practical -- suppressed at the point of use with a targeted
    `# ty: ignore[rule-name]` and a comment explaining why. `ty` gates `inv lint all`.
    """
    if target is None:
        paths = ['apps', 'config', 'admin', 'supervise.py', 'gunicorn.conf.py']
    elif target == _ADMIN_TARGET:
        paths = ['admin']
    elif target in {a.value for a in DjangoApp}:
        paths = [f'apps/{target}']
    else:
        valid = ', '.join([*(a.value for a in DjangoApp), _ADMIN_TARGET])
        raise typer.BadParameter(f'Unknown target {target!r}; expected one of: {valid}.')

    run('ty', 'check', *paths, dry=dry, cwd=PROJECT_ROOT)


@app.command(name='imports')
def lint_imports(dry: DryAnnotation = False):
    """
    Check the dependency rules between apps with `import-linter`.

    Contracts are in `[tool.importlinter]` in `pyproject.toml`.
    """
    run('lint-imports', dry=dry, cwd=PROJECT_ROOT)


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
    lint_imports(dry=dry)

    logger.info('Done')


if __name__ == '__main__':
    app()
