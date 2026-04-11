import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from itertools import chain
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.text import Text

from . import PROJECT_ROOT
from .environment import Environment
from .web_app import WebApp

EnvironmentAnnotation = Annotated[
    Environment | None,
    typer.Argument(help='Environment to use.', show_default=False),
]

WebAppAnnotation = Annotated[
    WebApp,
    typer.Argument(help='Web app to use.', show_default=False),
]
DryAnnotation = Annotated[
    bool,
    typer.Option(
        help='Show the command that would be run without running it.',
        show_default=False,
    ),
]


class OS(str, Enum):
    """Operating System."""

    Linux = 'linux'
    MacOS = 'mac'
    Windows = 'win'


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


def read_env_file(environment: Environment) -> dict[str, str]:
    return read_env_file_from_path(PROJECT_ROOT / f'.env.{environment.value}')


def select_environment(
    environment: Environment | None = None,
    set_env: bool = True,
    **select_enum_kwargs,
) -> Environment:
    """
    Select an environment and load its `.env` file into `os.environ`.

    This project used to rely on `python-dotenv` for this. We keep the behavior (populate
    `os.environ`) without depending on `python-dotenv`.
    """

    from textual_searchable_selectionlist.options import SelectionStrategy
    from textual_searchable_selectionlist.select import select_enum

    if environment:
        env = Environment(environment)
    else:
        try:
            selected = select_enum(
                Environment,
                selection_strategy=SelectionStrategy.ONE,
                title='Environment',
                select_by='value',
                **select_enum_kwargs,
            )
        except Exception as e:
            logger.error(f'Error selecting environment: {type(e).__name__}: {e}')
            raise typer.Exit(1)

        if not selected:
            logger.error('No environment selected.')
            raise typer.Exit(1)

        env = selected[0]

    if set_env:
        logger.debug(f'Setting environment `{env.value}`')
        os.environ['ENVIRONMENT'] = env.value

        resolved_env_path = PROJECT_ROOT / f'.env.{env.value}'
        env_values = read_env_file_from_path(resolved_env_path)
        os.environ.update(env_values)
    else:
        logger.debug(f'Selected environment `{env.value}`')

    return env


def set_environment(
    environment: Environment | str | None = None,
    web_app: WebApp | str | None = None,
) -> Environment:
    """Load the selected common and app-specific environment files into `os.environ`."""
    from .environment import select_env

    try:
        resolved_web_app = (
            web_app if web_app is None or isinstance(web_app, WebApp) else WebApp(web_app)
        )
    except ValueError:
        logger.error(f'Unknown web app: {web_app}')
        raise typer.Exit(1)

    selection = select_env(PROJECT_ROOT, environment=environment, web_app=resolved_web_app)

    for warning in selection.warnings:
        logger.warning(warning)

    if selection.errors or not selection.environment:
        for error in selection.errors:
            logger.error(error)
        raise typer.Exit(1)

    os.environ['ENVIRONMENT'] = selection.environment.value
    for env_path in selection.all_env_paths:
        os.environ.update(read_env_file_from_path(env_path))

    return selection.environment


def get_os() -> OS:
    """
    Similar to `sys.platform` and `platform.system()`, but less ambiguous by returning an Enum
    instead of a string.

    Doesn't make granular distinctions of linux variants, OS versions, etc.
    """
    if sys.platform == 'darwin':
        return OS.MacOS
    if sys.platform == 'win32':
        return OS.Windows
    return OS.Linux


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
    logger.info(' '.join(args_filtered))

    if dry:
        return None

    defaults = dict(
        cwd=PROJECT_ROOT,
        capture_output=False,
        text=True,
        check=True,
    )
    final_kwargs = defaults | kwargs

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


def run_async(*args, dry: bool = False, **kwargs) -> subprocess.Popen | None:
    """
    Starts the process and continues code execution.

    Use the following checks::

        process.poll()              # Returns None if still running, else return code
        process.wait()              # Wait for completion (blocking)
        process.terminate()         # Send SIGTERM (graceful)
        process.kill()              # Send SIGKILL (force)
        process.returncode          # Access return code after completion

    See `subprocess.Popen(...)` for more details.
    """
    logger.info(' '.join(map(str, args)))

    if dry:
        return None

    defaults = dict(
        cwd=PROJECT_ROOT,
    )

    try:
        return subprocess.Popen(args, **(defaults | kwargs))
    except subprocess.CalledProcessError as e:
        logger.error(e)
        raise typer.Exit(1)


def is_package_installed(package_name: str) -> bool:
    """Check if a Python package is installed."""
    import importlib.util

    return importlib.util.find_spec(package_name) is not None


def install_package(package: str, package_install: str | None = None, dry: bool = False):
    """
    Install a Python package if not already installed.

    :param package: Name of the package to check/install.
    :param package_install: Name of the package to install, if different from the name to check.
    :param dry: Show the command that would be run without running it.
    """
    if is_package_installed(package):
        logger.debug(f'Package `{package}` is already installed.')
        return

    run(sys.executable, '-m', 'pip', 'install', package_install or package, dry=dry)


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
