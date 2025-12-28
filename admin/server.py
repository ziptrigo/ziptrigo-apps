#!python
"""
Run Django server.

Equivalent to ``python manage.py runserver``, just makes it easier to set the environment when
multiple environment files exist (which should be the case in development machines only).
"""
import os
import sys

import typer

from admin import PROJECT_ROOT
from admin.utils import DryAnnotation, Environment, EnvironmentAnnotation, run, AppAnnotation

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)


@app.command(name='run')
def server_run(
    app: AppAnnotation,
    environment: EnvironmentAnnotation = Environment.DEV,
    dry: DryAnnotation = False,
):
    """
    Run Django server.

    Equivalent to ``python manage.py runserver``, just makes it easier to set the environment when
    multiple environment files exist (which should be the case in development machines only).

    To use more options from ``manage.py``, use ``python manage.py runserver`` directly.
    Set the ``ENVIRONMENT`` environment variable to the desired environment.
    """
    python_exe = sys.executable
    run(
        python_exe,
        'manage.py',
        'runserver',
        dry=dry,
        cwd=PROJECT_ROOT / app.value,
        env=os.environ | {'ENVIRONMENT': environment.value},
    )


if __name__ == '__main__':
    app()
