import logging
import os
import subprocess
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.text import Text

from . import PROJECT_ROOT
from .environment import Environment

EnvironmentAnnotation = Annotated[
    Environment | None,
    typer.Argument(help='Environment to use.', show_default=False),
]

DryAnnotation = Annotated[
    bool,
    typer.Option(
        help='Show the command that would be run without running it.',
        show_default=False,
    ),
]


class NoHighlightRichHandler(RichHandler):
    """Custom RichHandler that completely disables highlighting."""

    def render_message(self, record, message):
        """Override to disable auto-highlighting while keeping markup."""
        from rich.text import Text

        # Process markup but don't apply highlighting
        if self.markup:
            return Text.from_markup(message)
        return Text(message)


@dataclass
class StripOutput:
    strip_ansi: bool = True
    normal_strip: bool = True
    extra_chars: str | None = None

    def strip(self, text: str) -> str:
        if self.strip_ansi:
            text = strip_ansi(text)
        if self.normal_strip:
            text = text.strip()
        if self.extra_chars:
            text = text.strip(self.extra_chars)

        return text


def read_env_file_from_path(env_path: Path) -> dict[str, str]:
    """
    Read a `.env` file. Minimal parser, no dependencies.
    Does not update environment.

    Rules:

    - Ignores blank lines and comments.
    - Supports `export KEY=VALUE`.
    - Strips surrounding single/double quotes.
    """
    if not env_path.exists():
        raise FileNotFoundError(f'Env file not found: {env_path}')

    env = {}
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        if line.startswith('export '):
            line = line.removeprefix('export ').lstrip()

        key, sep, value = line.partition('=')
        if not sep:
            continue

        key = key.strip()
        value = value.strip()
        if (value.startswith("'") and value.endswith("'")) or (
            value.startswith('"') and value.endswith('"')
        ):
            value = value[1:-1]

        env[key] = value

    return env


def write_private_file(path: Path, text: str) -> None:
    """
    Write `text` to `path` so that only the owner can read it, whatever the file looked like before.

    Used for the CLIs' token files. Passing `0o600` to `os.open` only applies when the file is
    *created*; one that already exists (made by hand, or copied with a looser umask) keeps its old
    mode through `O_TRUNC`, so the mode is set again on the open descriptor. `O_NOFOLLOW` refuses
    to write through a symlink (an `OSError`), so a planted link can't redirect the token elsewhere.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, 'w') as fh:
        fh.write(text)


def set_environment(environment: Environment | str | None = None) -> Environment:
    """Load the selected environment file into `os.environ`."""
    from .environment import select_env

    selection = select_env(PROJECT_ROOT, environment=environment)

    for warning in selection.warnings:
        logger.warning(warning)

    if selection.errors or not selection.environment:
        for error in selection.errors:
            logger.error(error)
        raise typer.Exit(1)

    os.environ['ENVIRONMENT'] = selection.environment.value
    if selection.env_path:
        os.environ.update(read_env_file_from_path(selection.env_path))

    return selection.environment


# -- The JWT HTTP-client CLIs (`qrcode`, `filetransfer`, `receipts`) --------------------------
#
# They talk to a running site over its API, so which site, which token and where the password
# comes from are all decided here once. Everything below is read when a command runs, *after*
# `select_environment` has loaded the `.env.<environment>` file, never at import time.

#: Where the CLIs keep their token files (`token_file`); a test points it at a temp directory.
TOKEN_DIR = Path.home()

# The environment the running command selected (`select_environment`); it names the token file.
_environment = 'local'


def select_environment(environment: Environment | str | None) -> str:
    """Load the selected environment file (`set_environment`) and remember which environment it
    is, for `token_file` and `current_environment`. Every command of those CLIs calls this first."""
    global _environment
    selected = set_environment(environment)
    _environment = str(selected or os.environ.get('ENVIRONMENT') or 'local')
    _announce_site()
    return _environment


def _announce_site() -> None:
    """Say on stderr which site the command is about to talk to, and warn when a stray
    `API_BASE_URL` overrides the environment file's `BASE_URL`: the token file is chosen by
    environment name, so the token would be sent to whatever host that variable names."""
    site = site_url()
    override = os.environ.get('API_BASE_URL', '').rstrip('/')
    file_url = os.environ.get('BASE_URL', '').rstrip('/')
    if override and file_url and override != file_url:
        typer.echo(
            f'Warning: API_BASE_URL ({override}) overrides the {_environment} environment '
            f"file's BASE_URL ({file_url}); the {_environment} token will be sent to {override}.",
            err=True,
        )
    typer.echo(f'Site: {site} ({_environment})', err=True)


def current_environment() -> str:
    """The environment `select_environment` last selected (`local` before any)."""
    return _environment


def site_url() -> str:
    """The site the CLIs talk to: `API_BASE_URL`, else the selected environment file's `BASE_URL`,
    else the local dev server -- so `<cli> prod ...` reaches prod, not localhost."""
    base = os.environ.get('API_BASE_URL') or os.environ.get('BASE_URL') or 'http://localhost:8000'
    return base.rstrip('/')


def api_url(path: str) -> str:
    """`<site>/api<path>`, e.g. `api_url('/ft/transfers/')`."""
    return f'{site_url()}/api{path}'


def _legacy_token_file(cli: str) -> Path:
    return TOKEN_DIR / f'.{cli}_token'


def token_file(cli: str) -> Path:
    """`cli`'s token file for the selected environment, `~/.<cli>_token.<environment>`. One per
    environment, and one per CLI: a token is only valid for the server that issued it, so a shared
    file would send the prod token to the local server and the other way round, and each CLI logs
    in independently (possibly as another account). The single `~/.<cli>_token` of earlier
    versions is no longer read."""
    legacy = _legacy_token_file(cli)
    return legacy.with_name(f'{legacy.name}.{_environment}')


def get_token(cli: str) -> str | None:
    """The stored authentication token, if any."""
    path = token_file(cli)
    if path.exists():
        return path.read_text().strip()
    return None


def save_token(cli: str, token: str) -> None:
    """Save the authentication token with `0600` permissions, also when the file already exists
    with a looser mode (see `write_private_file`)."""
    write_private_file(token_file(cli), token)


def get_headers(cli: str) -> dict:
    """Headers carrying the stored token; exits with a hint to log in when there is none."""
    token = get_token(cli)
    if not token:
        logger.error(f'Not authenticated on {_environment}. Please login first.')
        if _legacy_token_file(cli).exists():
            logger.warning(
                f'{_legacy_token_file(cli)} is from an older version and no longer used: '
                'login again, then delete it.'
            )
        raise typer.Exit(1)
    return {'Authorization': f'Bearer {token}'}


def password_from_prompt_or_env(password: str | None, env_var: str) -> str:
    """A password argument that defaults to `None` (rather than being required positionally) is
    entered with the terminal's echo hidden when omitted -- typing it in the clear as a plain CLI
    argument leaves it sitting in the shell's history and briefly visible to anyone on the same
    machine running `ps`. `env_var` keeps non-interactive use (scripts, CI) working without either
    of those: `login` checks it before falling back to an interactive prompt."""
    if password is not None:
        return password
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env
    return typer.prompt('Password', hide_input=True)


def run(
    *args, dry: bool = False, strip_output: StripOutput | None = StripOutput(), **kwargs
) -> subprocess.CompletedProcess | None:
    """
    Run a CLI command synchronously (i.e., wait for the command to finish) and return the result.

    This function is a wrapper around `subprocess.run(...)`.

    If you need access to the output, add the `capture_output=True` argument and do
    `.stdout` to get the output as a string.

    Note that `stdout` and `stderr` will be stripped of ANSI escape sequences by default.
    """
    args_filtered = [x for arg in args if arg is not None and (x := str(arg).strip())]  # noqa
    # Parens, not brackets: the logging handler renders messages as Rich markup, where `[...]`
    # is a style tag rather than literal text.
    cwd = kwargs.get('cwd', PROJECT_ROOT)
    logger.info(f'({cwd}) {" ".join(args_filtered)}')

    if dry:
        return None

    defaults = dict(
        cwd=PROJECT_ROOT,
        capture_output=False,
        text=True,
        check=True,
    )
    # Explicitly `dict[str, Any]`: a runtime dict merge loses the literal types (`text=True`,
    # `check=True`, ...) that `subprocess.run`'s `@overload`s are keyed on -- there's no way to
    # thread that through without hand-writing every overload here too. `Any` tells ty to accept
    # whatever `subprocess.run` resolves to at the call below rather than trying (and failing) to
    # match an overload; it's not a real bug, just the same call
    # `subprocess.run(*args_filtered, text=True, check=True, ...)` would make.
    final_kwargs: dict[str, Any] = defaults | kwargs

    try:
        result = subprocess.run(args_filtered, **final_kwargs)
    except subprocess.CalledProcessError as e:
        msg = str(e)
        if e.stdout:
            msg += f'\nSTDOUT:\n{e.stdout}'
        if e.stderr:
            msg += f'\nSTDERR:\n{e.stderr}'
        logger.error(msg)
        raise typer.Exit(1)

    if final_kwargs.get('capture_output') and strip_output:
        result.stdout = strip_output.strip(result.stdout)
        result.stderr = strip_output.strip(result.stderr)

    return result


def multiple_parameters(parameter: str, *options) -> list[str]:
    return list(chain.from_iterable(zip([parameter] * len(options), map(str, options))))


def strip_ansi(text: str) -> str:
    return Text.from_ansi(text).plain


def get_logger(name: str | None = 'typer-invoke', level=logging.DEBUG) -> logging.Logger:
    """Set up logging configuration with Rich handler and custom formatting."""
    _logger = logging.getLogger(name)
    _logger.setLevel(level)
    _logger.handlers.clear()

    console = Console(markup=True)

    handler = NoHighlightRichHandler(
        level=level,
        console=console,
        show_time=False,
        show_level=True,
        show_path=False,
        markup=True,
        rich_tracebacks=False,
    )

    formatter = logging.Formatter(fmt='%(message)s', datefmt='[%X]')
    handler.setFormatter(formatter)
    _logger.addHandler(handler)
    _logger.propagate = False

    return _logger


logger = get_logger()
