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
