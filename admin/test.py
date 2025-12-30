#!python
"""
Testing with pytest.
"""

import typer
from typing import Annotated

from admin.utils import DryAnnotation, run

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)


@app.command(name='unit')
def test_unit(dry: DryAnnotation = False):
    """
    Run unit tests.

    Unit test configuration in ``pyproject.toml``.
    """
    run('pytest', '.', dry=dry)


@app.command(name='e2e')
def test_e2e(
    headless: Annotated[bool, typer.Option(help='Run tests in headless mode.')] = True,
    dry: DryAnnotation = False,
):
    """
    Run end-to-end tests.

    Playwright tests.

    Test configuration in ``pytest_e2e.ini``.
    """
    args = ['pytest', '-c', 'pytest_e2e.ini']
    if not headless:
        args.append('--headed')
    run(*args, dry=dry)


if __name__ == '__main__':
    app()
