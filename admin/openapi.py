#!python
"""
Generate OpenAPI specification.
"""

import json
import os
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from django.utils.functional import Promise

from .utils import logger

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)


class Format(str, Enum):
    """Output format for OpenAPI specification."""

    JSON = 'json'
    YAML = 'yaml'


def _resolve_lazy(value):
    """
    Recursively force lazy translation proxies to plain strings.

    Django validators (e.g. `UnicodeUsernameValidator.message`, which ends up in the schema via
    `ninja_jwt`'s username field) wrap their text in `gettext_lazy`, producing
    `django.utils.functional.Promise` instances rather than `str`. Neither serializer below can
    handle them as-is: `json.dumps` doesn't know how to encode them, and `yaml.dump` falls back
    to its generic-object representer, which can't reconstruct them from `__reduce__` either.
    Resolving them up front, right after pulling the schema out of Ninja, sidesteps both.
    """
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, dict):
        return {key: _resolve_lazy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_lazy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_lazy(item) for item in value)
    return value


def setup_django():
    """Configure Django settings."""
    import django
    from django.apps import apps

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

    if not apps.ready:
        django.setup()


@app.command(name='generate')
def generate_openapi(
    format: Annotated[
        Format,
        typer.Option(
            help='Output format for the OpenAPI specification.',
            case_sensitive=False,
        ),
    ] = Format.YAML,
    file: Annotated[
        Path | None,
        typer.Option(
            help='File path to save the specification. If not provided, displays on screen.',
            show_default=False,
        ),
    ] = None,
):
    """
    Generate OpenAPI specification.

    Generates the OpenAPI schema in JSON or YAML format and optionally saves it to a file or
    displays it on the screen.
    """
    setup_django()

    from config.api import api

    try:
        logger.info(f'Generating OpenAPI schema in {format.value} format...')

        schema = _resolve_lazy(api.get_openapi_schema())

        # Convert to the desired format
        if format == Format.JSON:
            output = json.dumps(schema, indent=2)
        else:  # YAML
            import yaml

            output = yaml.dump(schema, default_flow_style=False, sort_keys=False)

        # Save to file or display
        if file:
            file_path = Path(file)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(output)
            logger.info(f'OpenAPI schema saved to {file_path.resolve()}')
        else:
            print(output)

    except Exception as e:
        logger.error(f'Failed to generate OpenAPI schema: {e}')
        raise typer.Exit(1)


if __name__ == '__main__':
    app()
