#!python
"""
File Transfer CLI

Command line interface for the file_transfer JWT API (`/api/ft/`, issue #55 phase 3), on the model
of `admin/qrcode.py`: log in, send files or folders with resumable direct-to-S3 multipart uploads,
and manage existing transfers (list, inspect, disable/enable, delete, change expiry/max
downloads/password, add recipients, resend).
"""

import base64
import hashlib
import os
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .utils import EnvironmentAnnotation, logger, set_environment

app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode='markdown',
)

# Configuration
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:8000')
# A separate token file from `admin/qrcode.py`'s `.qrcode_token`: each CLI logs in independently
# (possibly as a different account), so sharing one file would mean the last `login` of either
# command silently signs the other one out too.
TOKEN_FILE = Path.home() / '.filetransfer_token'


def get_token() -> Optional[str]:
    """Retrieve stored authentication token."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


def save_token(token: str):
    """Save authentication token to file."""
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)


def get_headers() -> dict:
    """Get headers with authentication token."""
    token = get_token()
    if not token:
        logger.error('Not authenticated. Please login first.')
        raise typer.Exit(1)
    return {'Authorization': f'Bearer {token}'}


def _raise_for_api_error(response) -> None:
    """`requests.Response.raise_for_status()`, but surfacing the API's own `{"detail": ...}`
    message (every error response in this API -- `ninja.errors.HttpError`, and the site-wide 429
    handler in `config/api.py` -- carries one) instead of just the bare status code."""
    if response.ok:
        return
    try:
        detail = response.json().get('detail', response.text)
    except ValueError:
        detail = response.text
    logger.error(f'{response.status_code}: {detail}')
    raise typer.Exit(1)


@app.command(name='login')
def filetransfer_login(environment: EnvironmentAnnotation, email: str, password: str):
    """
    Authenticate with the API and store the token.

    Example:
        filetransfer login dev me@example.com mypassword
    """
    import requests

    set_environment(environment)

    try:
        response = requests.post(
            f'{API_BASE_URL}/api/auth/login', json={'email': email, 'password': password}
        )
        response.raise_for_status()
        token = response.json()['access_token']
        save_token(token)
        logger.info('Successfully authenticated!')
    except requests.exceptions.RequestException as e:
        logger.error(f'Login failed: {e}')
        raise typer.Exit(1)


def _sha256_base64(data: bytes) -> str:
    """Base64-encoded SHA-256 digest of `data`, for the S3 per-part integrity check (spec section
    11) -- the server-side counterpart of `send.html`'s `sha256Base64` (there computed with
    `crypto.subtle` instead, since it runs in a browser)."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def _collect_files(paths: list[Path]) -> list[Path]:
    """Expand files and folders (recursively, sorted for deterministic output) into a flat list of
    files to send."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob('*') if p.is_file()))
        elif path.is_file():
            files.append(path)
        else:
            logger.error(f'Not found: {path}')
            raise typer.Exit(1)
    return files


def _find_resumable(existing_files: list[dict], name: str, size: int) -> dict | None:
    """Match a local file being (re-)sent to a file already on the draft, by name + size -- the
    CLI's counterpart of `resumable_upload.js`'s `findResumeMatch` (there also considers
    `lastModified` when the row has one; the CLI doesn't bother, since it re-reads the same local
    file path rather than asking a person to re-pick a file by hand)."""
    for existing in existing_files:
        if existing['name'] == name and existing['size'] == size:
            return existing
    return None


def _upload_file(headers: dict, transfer_id: str, path: Path, existing_files: list[dict]) -> None:
    """Upload one file to `transfer_id`'s draft, resuming it (via `.../resume/`) if a matching,
    not-yet-uploaded file is already on the draft -- see `_find_resumable`."""
    import requests

    name = path.name
    size = path.stat().st_size
    base = f'{API_BASE_URL}/api/ft/transfers/{transfer_id}/files'
    match = _find_resumable(existing_files, name, size)

    if match is not None and match['uploaded']:
        logger.info(f'{name}: already uploaded, skipping')
        return

    uploaded_parts: dict[int, str] = {}
    if match is not None:
        file_id = match['id']
        response = requests.post(f'{base}/{file_id}/resume/', headers=headers)
        _raise_for_api_error(response)
        resumed = response.json()
        part_size = resumed['part_size_bytes']
        part_count = resumed['part_count']
        uploaded_parts = {p['part_number']: p['etag'] for p in resumed['uploaded_parts']}
        if resumed['restarted']:
            logger.info(f'{name}: previous upload had expired, restarting it')
        elif uploaded_parts:
            logger.info(f'{name}: resuming ({len(uploaded_parts)}/{part_count} parts already up)')
    else:
        response = requests.post(
            f'{base}/',
            json={
                'name': name,
                'size': size,
                'client_last_modified': int(path.stat().st_mtime * 1000),
            },
            headers=headers,
        )
        _raise_for_api_error(response)
        added = response.json()
        file_id = added['file_id']
        part_size = added['part_size_bytes']
        part_count = added['part_count']

    final_parts: list[dict] = [
        {'part_number': number, 'etag': etag} for number, etag in uploaded_parts.items()
    ]

    missing = [n for n in range(1, part_count + 1) if n not in uploaded_parts]
    if missing:
        # Keyed separately by part number (rather than round-tripped through a single
        # `{'part_number': ..., 'checksum_sha256': ...}` dict per part) so every value stays a
        # single, consistent type -- `ty` otherwise infers a mixed `dict[str, int | str]` for that
        # shape and can no longer tell a `part_number` used as a `blobs`/`checksums` key apart from
        # one that's actually a `str`.
        checksums: dict[int, str] = {}
        blobs: dict[int, bytes] = {}
        with path.open('rb') as fh:
            for part_number in missing:
                fh.seek((part_number - 1) * part_size)
                blob = fh.read(part_size)
                blobs[part_number] = blob
                checksums[part_number] = _sha256_base64(blob)

        request_parts = [{'part_number': n, 'checksum_sha256': checksums[n]} for n in missing]
        response = requests.post(
            f'{base}/{file_id}/parts/', json={'parts': request_parts}, headers=headers
        )
        _raise_for_api_error(response)
        urls = response.json()['urls']

        for part_number in missing:
            checksum = checksums[part_number]
            put_response = requests.put(
                urls[str(part_number)],
                data=blobs[part_number],
                headers={'x-amz-checksum-sha256': checksum},
            )
            put_response.raise_for_status()
            final_parts.append(
                {
                    'part_number': part_number,
                    'etag': put_response.headers.get('ETag', ''),
                    'checksum_sha256': checksum,
                }
            )
            logger.info(f'{name}: part {part_number}/{part_count} uploaded')

    final_parts.sort(key=lambda part: part['part_number'])
    response = requests.post(
        f'{base}/{file_id}/complete/', json={'parts': final_parts}, headers=headers
    )
    _raise_for_api_error(response)
    logger.info(f'{name}: upload complete')


@app.command(name='send')
def filetransfer_send(
    environment: EnvironmentAnnotation,
    paths: list[Path] = typer.Argument(..., help='Files or folders to send.'),
    to: list[str] = typer.Option(..., '--to', help='Recipient email address (repeatable).'),
    message: str = typer.Option('', '--message', '-m', help='Message shown to recipients.'),
    expiry: str = typer.Option(
        'none', '--expiry', help='Expiry: 1, 5, 15 or 30 (days), or "none".'
    ),
    max_downloads: Optional[int] = typer.Option(
        None, '--max-downloads', help='Cap on total downloads (unset = uncapped).'
    ),
    password: str = typer.Option('', '--password', help='Optional download password.'),
    draft_id: Optional[str] = typer.Option(
        None,
        '--draft-id',
        help='Resume an interrupted send: the draft id printed by a previous, failed run.',
    ),
):
    """
    Send files or folders: create (or resume) a draft, upload every file directly to S3 with
    resumable multipart uploads, then finalize.

    If the command is interrupted partway through (a dropped connection, say), re-run it with
    `--draft-id <the id it printed>` -- already-uploaded files are skipped, and a partially
    uploaded file resumes from whichever parts S3 already has instead of re-uploading everything.

    Example:
        filetransfer send report.pdf --to a@example.com --to b@example.com
        filetransfer send ./photos --to a@example.com --draft-id 1b6e...  # resume
    """
    import requests

    set_environment(environment)
    headers = get_headers()

    files = _collect_files(list(paths))
    if not files:
        logger.error('No files found.')
        raise typer.Exit(1)

    if draft_id:
        response = requests.get(f'{API_BASE_URL}/api/ft/transfers/{draft_id}', headers=headers)
        _raise_for_api_error(response)
        transfer = response.json()
        if transfer['status'] != 'draft':
            logger.error(f'{draft_id} is no longer a draft (status: {transfer["status"]}).')
            raise typer.Exit(1)
        transfer_id = transfer['id']
        existing_files = transfer['files']
        logger.info(f'Resuming draft {transfer_id}')
    else:
        response = requests.post(f'{API_BASE_URL}/api/ft/transfers/', json={}, headers=headers)
        _raise_for_api_error(response)
        transfer_id = response.json()['id']
        existing_files = []
        logger.info(f'Draft created: {transfer_id}')

    try:
        for path in files:
            _upload_file(headers, transfer_id, path, existing_files)
    except requests.exceptions.RequestException as e:
        logger.error(f'{e}')
        logger.error(f'Re-run with --draft-id {transfer_id} to resume.')
        raise typer.Exit(1)

    response = requests.post(
        f'{API_BASE_URL}/api/ft/transfers/{transfer_id}/send',
        json={
            'recipients': to,
            'message': message,
            'expiry_choice': expiry,
            'max_downloads': max_downloads,
            'password': password,
        },
        headers=headers,
    )
    _raise_for_api_error(response)
    result = response.json()
    logger.info('Sent!')
    logger.info(f'Link: {result["download_url"]}')


@app.command(name='list')
def filetransfer_list(
    environment: EnvironmentAnnotation,
    filter: str = typer.Option(  # noqa: A002 -- matches the API's own `?filter=` query param
        'active', '--filter', help='"active", "ended" or "all".'
    ),
):
    """
    List your transfers.

    Example:
        filetransfer list
        filetransfer list --filter ended
    """
    import requests

    set_environment(environment)

    response = requests.get(
        f'{API_BASE_URL}/api/ft/transfers/', params={'filter': filter}, headers=get_headers()
    )
    _raise_for_api_error(response)
    transfers = response.json()['results']

    if not transfers:
        logger.info('No transfers found.')
        return

    table = Table(title='Your Transfers')
    table.add_column('ID', style='cyan')
    table.add_column('Name', style='white')
    table.add_column('Status', style='green')
    table.add_column('Downloads', style='magenta')
    table.add_column('Expires', style='blue')

    for transfer in transfers:
        table.add_row(
            transfer['id'][:8],
            transfer['display_name'],
            transfer['status'],
            str(transfer['download_count']),
            transfer['expires_at'] or 'never',
        )

    Console().print(table)


@app.command(name='show')
def filetransfer_show(environment: EnvironmentAnnotation, transfer_id: str):
    """
    Show details of a specific transfer.

    Example:
        filetransfer show 1b6e2f4a-...
    """
    import requests

    set_environment(environment)

    response = requests.get(f'{API_BASE_URL}/api/ft/transfers/{transfer_id}', headers=get_headers())
    _raise_for_api_error(response)
    transfer = response.json()

    console = Console()
    console.print(f'[cyan]ID:[/cyan] {transfer["id"]}')
    console.print(f'[cyan]Name:[/cyan] {transfer["display_name"]}')
    console.print(f'[cyan]Status:[/cyan] {transfer["status"]}')
    console.print(f'[cyan]Recipients:[/cyan] {", ".join(transfer["recipients"]) or "(none)"}')
    console.print(f'[cyan]Expires:[/cyan] {transfer["expires_at"] or "never"}')
    console.print(f'[cyan]Max downloads:[/cyan] {transfer["max_downloads"] or "uncapped"}')
    console.print(f'[cyan]Downloads so far:[/cyan] {transfer["download_count"]}')
    console.print(f'[cyan]Password protected:[/cyan] {transfer["has_password"]}')
    console.print(f'[cyan]Notify on download:[/cyan] {transfer["notify_on_download"]}')
    console.print(f'[cyan]Credits charged:[/cyan] {transfer["credits_charged"]}')
    console.print(f'[cyan]Link:[/cyan] {transfer["download_url"]}')
    for file in transfer['files']:
        status = 'uploaded' if file['uploaded'] else 'not uploaded'
        console.print(f'  - {file["name"]} ({file["size"]} bytes, {status})')


def _update(transfer_id: str, payload: dict) -> dict:
    import requests

    response = requests.patch(
        f'{API_BASE_URL}/api/ft/transfers/{transfer_id}', json=payload, headers=get_headers()
    )
    _raise_for_api_error(response)
    return response.json()


@app.command(name='disable')
def filetransfer_disable(environment: EnvironmentAnnotation, transfer_id: str):
    """Disable a transfer: its link stops working, and it stops being metered."""
    set_environment(environment)
    _update(transfer_id, {'disabled': True})
    logger.info(f'{transfer_id} disabled.')


@app.command(name='enable')
def filetransfer_enable(environment: EnvironmentAnnotation, transfer_id: str):
    """Re-enable a disabled or suspended transfer."""
    set_environment(environment)
    _update(transfer_id, {'disabled': False})
    logger.info(f'{transfer_id} re-enabled.')


@app.command(name='set-expiry')
def filetransfer_set_expiry(
    environment: EnvironmentAnnotation,
    transfer_id: str,
    expiry: str = typer.Argument(..., help='1, 5, 15, 30 (days), "custom" or "none".'),
    date: Optional[str] = typer.Option(
        None, '--date', help='ISO 8601 datetime, required when expiry is "custom".'
    ),
):
    """
    Change a transfer's expiry.

    Example:
        filetransfer set-expiry 1b6e2f4a-... 30
        filetransfer set-expiry 1b6e2f4a-... none
        filetransfer set-expiry 1b6e2f4a-... custom --date 2026-12-31T00:00:00Z
    """
    set_environment(environment)
    payload = {'expiry_choice': expiry}
    if date:
        payload['expiry_date'] = date
    _update(transfer_id, payload)
    logger.info(f'{transfer_id} expiry updated.')


@app.command(name='set-max-downloads')
def filetransfer_set_max_downloads(
    environment: EnvironmentAnnotation,
    transfer_id: str,
    max_downloads: Optional[int] = typer.Argument(None, help='Omit for uncapped.'),
):
    """
    Change a transfer's max-downloads cap.

    Example:
        filetransfer set-max-downloads 1b6e2f4a-... 5
        filetransfer set-max-downloads 1b6e2f4a-...
    """
    set_environment(environment)
    _update(transfer_id, {'max_downloads': max_downloads})
    logger.info(f'{transfer_id} max downloads updated.')


@app.command(name='set-password')
def filetransfer_set_password(environment: EnvironmentAnnotation, transfer_id: str, password: str):
    """
    Set (or change) a transfer's download password.

    Example:
        filetransfer set-password 1b6e2f4a-... sekret
    """
    set_environment(environment)
    _update(transfer_id, {'password': password})
    logger.info(f'{transfer_id} password set.')


@app.command(name='remove-password')
def filetransfer_remove_password(environment: EnvironmentAnnotation, transfer_id: str):
    """
    Remove a transfer's download password.

    Example:
        filetransfer remove-password 1b6e2f4a-...
    """
    set_environment(environment)
    _update(transfer_id, {'remove_password': True})
    logger.info(f'{transfer_id} password removed.')


@app.command(name='add-recipients')
def filetransfer_add_recipients(
    environment: EnvironmentAnnotation,
    transfer_id: str,
    to: list[str] = typer.Option(..., '--to', help='Recipient email address (repeatable).'),
):
    """
    Add recipients to an existing transfer (and email them).

    Example:
        filetransfer add-recipients 1b6e2f4a-... --to a@example.com --to b@example.com
    """
    import requests

    set_environment(environment)

    response = requests.post(
        f'{API_BASE_URL}/api/ft/transfers/{transfer_id}/recipients',
        json={'recipients': to},
        headers=get_headers(),
    )
    _raise_for_api_error(response)
    logger.info(f'{transfer_id} recipients updated: {", ".join(response.json()["recipients"])}')


@app.command(name='resend')
def filetransfer_resend(
    environment: EnvironmentAnnotation,
    transfer_id: str,
    recipient_id: str = typer.Argument(..., help='Recipient id, from `filetransfer show`.'),
):
    """
    Resend the transfer-sent email to one recipient.

    Example:
        filetransfer resend 1b6e2f4a-... 9c3d1e2b-...
    """
    import requests

    set_environment(environment)

    response = requests.post(
        f'{API_BASE_URL}/api/ft/transfers/{transfer_id}/recipients/{recipient_id}/resend',
        headers=get_headers(),
    )
    _raise_for_api_error(response)
    logger.info('Email resent.')


@app.command(name='delete')
def filetransfer_delete(environment: EnvironmentAnnotation, transfer_id: str):
    """
    Delete a transfer now (its files are removed immediately).

    Example:
        filetransfer delete 1b6e2f4a-...
    """
    import requests

    set_environment(environment)

    response = requests.delete(
        f'{API_BASE_URL}/api/ft/transfers/{transfer_id}', headers=get_headers()
    )
    if response.status_code != 204:
        _raise_for_api_error(response)
    logger.info(f'{transfer_id} deleted.')


if __name__ == '__main__':
    app()
