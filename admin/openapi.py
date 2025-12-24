#!python
"""
Generate OpenAPI specification.
"""
import json
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from admin import PROJECT_ROOT
from admin.utils import logger

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


def setup_django():
    """Configure Django settings."""
    import django
    from django.apps import apps

    sys.path.insert(0, str(PROJECT_ROOT.resolve()))
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
            help='File path to save the specification. If not provided, ' 'displays on screen.',
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

    from src.qr_code.api.router import api

    try:
        logger.info(f'Generating OpenAPI schema in {format.value} format...')

        schema = api.get_openapi_schema()

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
