#!python
"""
Create users and confirm their email addresses from the command line.

Both run a Django management command (`create_user`, `confirm_email`) in the environment you name,
so they act on that environment's database. `local` is the everyday case. `prod` uses `.env.prod`,
whose `DATABASE_URL` points at the server's own network: reach it with a tunnel (override
`DATABASE_URL` for the command) or run the management command inside the container instead.

Examples:

* `inv users create local me@example.com --superuser --confirm`
* `inv users create local staff@example.com 's3cret1' --staff`
* `inv users confirm local me@example.com`
"""

import os
import sys
from typing import Annotated

import typer

from . import PROJECT_ROOT
from .environment import Environment
from .utils import DryAnnotation, password_from_prompt_or_env, run

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

PASSWORD_ENV_VAR = 'USERS_PASSWORD'


def _manage(environment: Environment, args: list[str], extra_env: dict[str, str], dry: bool):
    run(
        sys.executable,
        'manage.py',
        *args,
        dry=dry,
        cwd=PROJECT_ROOT,
        env=os.environ | {'ENVIRONMENT': environment.value} | extra_env,
    )


@app.command(name='create')
def users_create(
    environment: Annotated[Environment, typer.Argument(help='Environment (database) to use.')],
    email: Annotated[str, typer.Argument(help="The new user's email address (their login).")],
    password: Annotated[
        str | None,
        typer.Argument(
            help=f'The password. Omit it to be asked (echo hidden), or set `{PASSWORD_ENV_VAR}`: '
            'a password given here stays in your shell history.',
            show_default=False,
        ),
    ] = None,
    superuser: Annotated[
        bool, typer.Option('--superuser', help='Create a superuser (also staff).')
    ] = False,
    staff: Annotated[
        bool, typer.Option('--staff', help='Create a staff user (not a superuser).')
    ] = False,
    confirm: Annotated[
        bool, typer.Option('--confirm', help="Confirm the user's email after creating it.")
    ] = False,
    dry: DryAnnotation = False,
):
    """
    Create a user. The password must pass the sign-up rules (6 characters and a digit).

    `--superuser` and `--staff` are mutually exclusive. Without `--confirm` the email is left
    unconfirmed, and password sign-in needs a confirmed one: confirm it here or with
    `inv users confirm`.
    """
    if superuser and staff:
        raise typer.BadParameter('--superuser and --staff cannot be used together.')

    password = password_from_prompt_or_env(password, PASSWORD_ENV_VAR)
    args = ['create_user', email]
    if superuser:
        args.append('--superuser')
    if staff:
        args.append('--staff')
    if confirm:
        args.append('--confirm')
    # The password goes to the child through its environment, never as an argument (`ps`).
    _manage(environment, args, {PASSWORD_ENV_VAR: password}, dry)


@app.command(name='confirm')
def users_confirm(
    environment: Annotated[Environment, typer.Argument(help='Environment (database) to use.')],
    email: Annotated[str, typer.Argument(help="The user's email address.")],
    dry: DryAnnotation = False,
):
    """
    Confirm a user's email address, as following the emailed link would (the same side effects,
    such as a promo code's credits being granted). An already confirmed account is left alone.
    """
    _manage(environment, ['confirm_email', email], {}, dry)


if __name__ == '__main__':
    app()
