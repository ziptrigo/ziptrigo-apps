#!python
"""
QR Code Generator CLI

QR Code CLI - Command line interface for QR code generation and management.

This CLI provides commands to interact with the QR code service.
"""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .utils import (
    EnvironmentAnnotation,
    api_url,
    get_headers,
    logger,
    password_from_prompt_or_env,
    save_token,
    select_environment,
    site_url,
)

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)


# The token lives in `~/.qrcode_token.<environment>` (`admin.utils.token_file`); the single
# `~/.qrcode_token` of earlier versions is no longer read: log in again, then delete it.
CLI = 'qrcode'


@app.command(name='login')
def qrcode_login(
    environment: EnvironmentAnnotation,
    email: str,
    password: Optional[str] = typer.Argument(
        None, help='Password. Omit to be prompted, or set QRCODE_PASSWORD.'
    ),
):
    """
    Authenticate with the API and store the token.

    Example:
        qrcode login local me@example.com
    """
    import requests

    select_environment(environment)
    password = password_from_prompt_or_env(password, 'QRCODE_PASSWORD')

    try:
        response = requests.post(
            api_url('/auth/login'), json={'email': email, 'password': password}
        )
        response.raise_for_status()
        token = response.json()['access_token']
        save_token(CLI, token)
        logger.info('Successfully authenticated!')
    except (requests.exceptions.RequestException, OSError) as e:
        logger.error(f'Login failed: {e}')
        raise typer.Exit(1)


@app.command(name='create')
def qrcode_create(
    environment: EnvironmentAnnotation,
    url: Optional[str] = typer.Option(None, '--url', '-u', help='URL to encode'),
    data: Optional[str] = typer.Option(None, '--data', '-d', help='Data to encode'),
    format: str = typer.Option('png', '--format', '-f', help='Output format (png, svg, jpeg)'),
    size: int = typer.Option(10, '--size', '-s', help='QR code size scale'),
    error_correction: str = typer.Option(
        'M', '--error', '-e', help='Error correction level (L, M, Q, H)'
    ),
    border: int = typer.Option(4, '--border', '-b', help='Border size'),
    bg_color: str = typer.Option('white', '--bg-color', help='Background color'),
    fg_color: str = typer.Option('black', '--fg-color', help='Foreground color'),
    shorten: bool = typer.Option(False, '--shorten', help='Use URL shortening'),
):
    """
    Create a new QR code.

    Example:
        qrcode create --url https://example.com --shorten
        qrcode create --data "Hello World" --format svg
    """
    import requests

    select_environment(environment)

    if not url and not data:
        logger.error('Error: Either --url or --data must be provided')
        raise typer.Exit(1)

    payload = {
        'qr_format': format,
        'size': size,
        'error_correction': error_correction,
        'border': border,
        'background_color': bg_color,
        'foreground_color': fg_color,
        'use_url_shortening': shorten,
    }

    if url:
        payload['url'] = url
    else:
        payload['data'] = data

    try:
        response = requests.post(api_url('/qr/'), json=payload, headers=get_headers(CLI))
        response.raise_for_status()
        result = response.json()

        logger.info('QR Code created successfully!')
        logger.info(f'ID: {result["id"]}')
        if result.get('short_code'):
            logger.info(f'Short URL: {site_url()}/go/{result["short_code"]}/')

    except requests.exceptions.RequestException as e:
        logger.error(f'Failed to create QR code: {e}')
        if e.response is not None and hasattr(e.response, 'text'):
            logger.error(f'{e.response.text}')
        raise typer.Exit(1)


@app.command(name='list')
def qrcode_list(
    environment: EnvironmentAnnotation,
):
    """
    List all QR codes for the authenticated user.

    Example:
        qrcode list
    """
    import requests

    select_environment(environment)

    try:
        response = requests.get(api_url('/qr/'), headers=get_headers(CLI))
        response.raise_for_status()
        qrcodes = response.json()

        if not qrcodes:
            logger.info('No QR codes found.')
            return

        table = Table(title='Your QR Codes')
        table.add_column('ID', style='cyan')
        table.add_column('Content', style='white')
        table.add_column('Format', style='green')
        table.add_column('Scans', style='magenta')
        table.add_column('Created', style='blue')

        for qr in qrcodes:
            content = qr['content'][:50] + '...' if len(qr['content']) > 50 else qr['content']
            table.add_row(
                str(qr['id'])[:8],
                content,
                qr['qr_format'],
                str(qr['scan_count']),
                qr['created_at'][:10],
            )

        console = Console()
        console.print(table)

    except requests.exceptions.RequestException as e:
        logger.error(f'Failed to list QR codes: {e}')
        raise typer.Exit(1)


@app.command(name='get')
def qrcode_get(environment: EnvironmentAnnotation, qr_id: str):
    """
    Get details of a specific QR code.

    Example:
        qrcode get abc123...
    """
    import requests

    select_environment(environment)

    try:
        response = requests.get(api_url(f'/qr/{qr_id}'), headers=get_headers(CLI))
        response.raise_for_status()
        qr = response.json()

        console = Console()
        console.print(f'[cyan]ID:[/cyan] {qr["id"]}')
        console.print(f'[cyan]Content:[/cyan] {qr["content"]}')
        console.print(f'[cyan]Format:[/cyan] {qr["qr_format"]}')
        console.print(f'[cyan]Size:[/cyan] {qr["size"]}')
        console.print(f'[cyan]Scans:[/cyan] {qr["scan_count"]}')
        console.print(f'[cyan]Image URL:[/cyan] {qr.get("image_url", "N/A")}')
        if qr.get('redirect_url'):
            console.print(f'[cyan]Redirect URL:[/cyan] {qr["redirect_url"]}')
        console.print(f'[cyan]Created:[/cyan] {qr["created_at"]}')

    except requests.exceptions.RequestException as e:
        logger.error(f'Failed to get QR code: {e}')
        raise typer.Exit(1)


@app.command(name='delete')
def qrcode_delete(environment: EnvironmentAnnotation, qr_id: str):
    """
    Delete a QR code.

    Example:
        qrcode delete abc123...
    """
    import requests

    select_environment(environment)

    try:
        response = requests.delete(api_url(f'/qr/{qr_id}'), headers=get_headers(CLI))
        response.raise_for_status()
        logger.info(f'QR code {qr_id} deleted successfully')

    except requests.exceptions.RequestException as e:
        logger.error(f'Failed to delete QR code: {e}')
        raise typer.Exit(1)


if __name__ == '__main__':
    app()
