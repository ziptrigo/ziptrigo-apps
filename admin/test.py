#!python
"""
Testing with pytest.
"""

import os
from typing import Annotated

import typer

from admin import PROJECT_ROOT
from admin.django_app import APPS_DIR, DjangoApp
from admin.utils import DryAnnotation, run

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

DjangoAppsAnnotation = Annotated[
    list[DjangoApp] | None,
    typer.Argument(
        help='One or more apps to test. If not set, all apps are tested.',
        show_default=False,
    ),
]


def _test_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault('ENVIRONMENT', 'dev')
    return env


@app.command(name='unit')
def test_unit(django_apps: DjangoAppsAnnotation = None, dry: DryAnnotation = False):
    """
    Run unit tests.

    Unit test configuration in `pyproject.toml`.
    """
    paths = [
        str((APPS_DIR / django_app.value).relative_to(PROJECT_ROOT))
        for django_app in django_apps or []
    ]
    run('pytest', *paths, dry=dry, cwd=PROJECT_ROOT, env=_test_env())


@app.command(name='e2e')
def test_e2e(
    headless: Annotated[bool, typer.Option(help='Run tests in headless mode.')] = True,
    dry: DryAnnotation = False,
):
    """
    Run end-to-end tests.

    Playwright tests.

    Test configuration in `pytest_e2e.ini`.
    """
    args = ['pytest', '-c', 'pytest_e2e.ini']
    if not headless:
        args.append('--headed')
    run(*args, dry=dry, cwd=PROJECT_ROOT, env=_test_env())


if __name__ == '__main__':
    app()
