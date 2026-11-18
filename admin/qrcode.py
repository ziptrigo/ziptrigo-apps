#!python
"""
QR Code Generator CLI

QR Code CLI - Command line interface for QR code generation and management.

This CLI provides commands to interact with the QR code service.
"""

from datetime import date, datetime
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from .utils import (
    EnvironmentAnnotation,
    api_error_text,
    api_request,
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


# -- Share links ---------------------------------------------------------------------------------
# A share link (`<site>/q/<code>`) shows a QR code to anyone who has the address and lets them
# download it. These commands call `/api/qr/{id}/links`; they go through `api_request` (default
# timeout, bounded retry on a short 429).

QRIdAnnotation = Annotated[str, typer.Argument(help='The QR code id (shown by `list`/`get`).')]


def _say(message: str) -> None:
    """Log `message` as it is: the logger renders Rich markup, which a user's text may contain."""
    logger.info(escape(message))


def _fail(message: str) -> typer.Exit:
    logger.error(escape(message))
    return typer.Exit(1)


def _console() -> Console:
    """A console for tables: the terminal's own width, or a wide one when output is piped."""
    console = Console()
    return console if console.is_terminal else Console(width=200)


def _call(method: str, path: str, **kwargs):
    """A request to `/api/qr<path>` that must succeed: its JSON, or `None` for a 204."""
    response = api_request(method, api_url(f'/qr{path}'), headers=get_headers(CLI), **kwargs)
    if not response.ok:
        raise _fail(api_error_text(response))
    return None if response.status_code == 204 else response.json()


def _expiry(value: str) -> str:
    """`--expires` as the API wants it. A bare date is sent as a date and the server reads it as
    the end of that day in your time zone; a time without an offset is read in your time zone."""
    value = value.strip()
    try:
        # A date first: `datetime.fromisoformat` accepts a bare date too (as midnight).
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        raise typer.BadParameter(
            'use an ISO date or date-time, like 2026-12-31 or 2026-12-31T18:00:00+00:00.'
        ) from None


def _link_state(link: dict) -> str:
    if link['revoked_at']:
        return 'revoked'
    return 'active' if link['active'] else 'expired'


def _people(link: dict) -> str:
    return ', '.join(person['email'] for person in link['recipients'])


@app.command(name='links')
def qrcode_links(environment: EnvironmentAnnotation, qr_id: QRIdAnnotation):
    """
    List a QR code's share links, with the people each was emailed to.

    Example:
        qrcode links local 1b6e2f4a-...
    """
    select_environment(environment)
    links = _call('get', f'/{qr_id}/links')
    if not links:
        _say('No share links. Make one with `share`.')
        return
    table = Table(title='Share links')
    table.add_column('ID', style='cyan', no_wrap=True, min_width=36)
    table.add_column('URL', no_wrap=True)
    table.add_column('Label')
    table.add_column('State', style='green', no_wrap=True)
    table.add_column('Expires', no_wrap=True)
    table.add_column('Emailed to')
    for link in links:
        table.add_row(
            link['id'],
            link['url'],
            Text(link['label']),
            _link_state(link),
            (link['expires_at'] or '')[:10],
            Text(_people(link)),
        )
    _console().print(table)


@app.command(name='share')
def qrcode_share(
    environment: EnvironmentAnnotation,
    qr_id: QRIdAnnotation,
    label: str = typer.Option('', '--label', help='A reminder of who it is for (never public).'),
    expires: Optional[str] = typer.Option(
        None, '--expires', help='ISO date (the end of that day) or date-time.'
    ),
    to: Optional[list[str]] = typer.Option(
        None, '--to', help='Email the link to this address (repeat for several).'
    ),
    message: str = typer.Option('', '--message', help='A note to add to the email (--to only).'),
):
    """
    Make a share link: anyone who has the address can see the QR code and download it. The
    address is printed. With --to, the link is also emailed (needs a confirmed email address on
    your account).

    Example:
        qrcode share local 1b6e2f4a-...
        qrcode share local 1b6e2f4a-... --label "for Sam" --expires 2026-12-31 --to sam@example.com
    """
    select_environment(environment)
    if message and not to:
        raise typer.BadParameter('--message goes with --to.')
    payload: dict = {}
    if label:
        payload['label'] = label
    if expires:
        payload['expires_at'] = _expiry(expires)
    link = _call('post', f'/{qr_id}/links', json=payload)
    _say(f'Made a share link: {link["url"]}')
    _say(f'Link ID: {link["id"]}')
    if to:
        body: dict = {'emails': list(to)}
        if message:
            body['message'] = message
        try:
            emailed = _call('post', f'/{qr_id}/links/{link["id"]}/email', json=body)
        except typer.Exit:
            _say('The link was made but not emailed (see the error above).')
            raise
        _say(f'Emailed the link to {_people(emailed)}.')


@app.command(name='unshare')
def qrcode_unshare(
    environment: EnvironmentAnnotation,
    qr_id: QRIdAnnotation,
    link_id: Annotated[str, typer.Argument(help='The link id (shown by `links`).')],
    yes: Annotated[bool, typer.Option('--yes', help='Do not ask first.')] = False,
):
    """
    Revoke a share link: it stops working for good (make another to share again).

    Example:
        qrcode unshare local 1b6e2f4a-... 7c9e6679-...
    """
    select_environment(environment)
    if not yes and not typer.confirm('Revoke this link? It stops working for good.'):
        raise _fail('Cancelled; nothing was changed.')
    _call('delete', f'/{qr_id}/links/{link_id}')
    _say('Revoked the link.')


if __name__ == '__main__':
    app()
