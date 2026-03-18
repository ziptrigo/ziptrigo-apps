#!python
"""
Testing with pytest.
"""

import os
from typing import Annotated

import typer

from admin import PROJECT_ROOT
from admin.utils import DryAnnotation, run
from common.web_app import WebApp

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

WebAppsAnnotation = Annotated[
    list[WebApp] | None,
    typer.Argument(
        help='One or more web apps to test. If not set, all apps are tested.',
        show_default=False,
    ),
]


def _test_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault('ENVIRONMENT', 'dev')
    pythonpath = env.get('PYTHONPATH')
    project_root = str(PROJECT_ROOT)
    env['PYTHONPATH'] = (
        project_root if not pythonpath else os.pathsep.join([project_root, pythonpath])
    )
    return env


def _selected_web_apps(web_apps: list[WebApp] | None) -> list[WebApp]:
    return list(WebApp) if web_apps is None else web_apps


@app.command(name='unit')
def test_unit(web_apps: WebAppsAnnotation = None, dry: DryAnnotation = False):
    """
    Run unit tests.

    Unit test configuration in `pyproject.toml`.
    """
    for web_app in _selected_web_apps(web_apps):
        run('pytest', 'tests', dry=dry, cwd=PROJECT_ROOT / web_app.value, env=_test_env())


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
    run(*args, dry=dry, env=_test_env())


if __name__ == '__main__':
    app()
