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
import time
from pathlib import Path

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

# Ordinary API calls; not the PUT of a (possibly large) file part -- see `PART_UPLOAD_TIMEOUT`.
REQUEST_TIMEOUT_SECONDS = 30
# A part can be up to `services.storage.PART_SIZE_BYTES` (64 MB); a slow connection legitimately
# needs longer than an ordinary API call to finish PUTting one.
PART_UPLOAD_TIMEOUT_SECONDS = 300
# Bounded retry for a 429 from the API's own rate limits (e.g. `FT_UPLOAD_USER`) -- an ordinary
# multi-file folder send legitimately calls `add_file`/`.../parts/` often enough to hit one.
MAX_RATE_LIMIT_RETRIES = 5
# How many parts' presigned PUT URLs to request at once, rather than every part of the whole file
# up front: a presigned URL is only valid for an hour
# (`services.storage.PUT_URL_EXPIRES_SECONDS`), so on a slow connection a large file's later parts
# could still be waiting when their URL expires if every URL were requested at time zero.
PART_URL_BATCH_SIZE = 8


def get_token() -> str | None:
    """Retrieve stored authentication token."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


def save_token(token: str) -> None:
    """Save the authentication token to file, created with `0600` permissions from the start --
    unlike `write_text()` followed by `chmod()`, which briefly leaves the file at the process's
    default (typically more permissive) mode between the two calls."""
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write(token)


def get_headers() -> dict:
    """Get headers with authentication token."""
    token = get_token()
    if not token:
        logger.error('Not authenticated. Please login first.')
        raise typer.Exit(1)
    return {'Authorization': f'Bearer {token}'}


def _password_from_prompt_or_env(
    password: str | None, env_var: str = 'FILETRANSFER_PASSWORD'
) -> str:
    """A password argument that defaults to `None` (rather than being required positionally) is
    entered with the terminal's echo hidden when omitted -- typing it in the clear as a plain CLI
    argument leaves it sitting in the shell's history and briefly visible to anyone on the same
    machine running `ps`. `env_var` keeps non-interactive use (scripts, CI) working without either
    of those: `login`/`set-password` check it before falling back to an interactive prompt."""
    if password is not None:
        return password
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env
    return typer.prompt('Password', hide_input=True)


def _retry_after_seconds(response) -> float:
    header = response.headers.get('Retry-After')
    try:
        return max(0.0, float(header))
    except TypeError, ValueError:
        return 1.0


def _request(method: str, url: str, **kwargs):
    """`requests.<method>(url, **kwargs)`, with a default timeout (`REQUEST_TIMEOUT_SECONDS`,
    overridable per call) and a bounded retry on 429 that honours the response's `Retry-After`
    header -- every HTTP call this CLI makes to our own API goes through here rather than calling
    `requests` directly, so both apply uniformly."""
    import requests

    kwargs.setdefault('timeout', REQUEST_TIMEOUT_SECONDS)
    func = getattr(requests, method)
    response = func(url, **kwargs)
    for _attempt in range(MAX_RATE_LIMIT_RETRIES):
        if response.status_code != 429:
            return response
        wait = _retry_after_seconds(response)
        logger.info(f'Rate limited; waiting {wait:.0f}s before retrying...')
        time.sleep(wait)
        response = func(url, **kwargs)
    return response


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
def filetransfer_login(
    environment: EnvironmentAnnotation,
    email: str,
    password: str | None = typer.Argument(
        None, help='Password. Omit to be prompted, or set FILETRANSFER_PASSWORD.'
    ),
):
    """
    Authenticate with the API and store the token.

    Example:
        filetransfer login dev me@example.com
        filetransfer login dev me@example.com mypassword
    """
    import requests

    set_environment(environment)
    password = _password_from_prompt_or_env(password)

    try:
        response = _request(
            'post', f'{API_BASE_URL}/api/auth/login', json={'email': email, 'password': password}
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


def _collect_files(paths: list[Path]) -> list[tuple[str, Path]]:
    """Expand files and folders (recursively, sorted for deterministic output) into a flat list of
    `(display name, path)` pairs to send.

    A file found while walking a folder is named by its path relative to that folder, rather than
    just its own file name -- two files with the same name in different subfolders would otherwise
    both report the same `name` to the API, which could then match a resume against the wrong one
    (see `_find_resumable`). A file passed directly on the command line keeps just its own name,
    matching how a single explicit file has always been sent.

    An empty file found while walking a folder is skipped, with a warning, rather than aborting
    the whole send -- a folder can easily contain a stray empty file (a `.gitkeep`, a placeholder)
    the sender never meant to include. A file named directly on the command line is still sent
    (and left to the API's own "file is empty" validation) unchanged: naming it was a deliberate
    choice.
    """
    files: list[tuple[str, Path]] = []
    for path in paths:
        if path.is_dir():
            for p in sorted(x for x in path.rglob('*') if x.is_file()):
                if p.stat().st_size == 0:
                    logger.warning(f'Skipping empty file: {p}')
                    continue
                files.append((str(p.relative_to(path)), p))
        elif path.is_file():
            files.append((path.name, path))
        else:
            logger.error(f'Not found: {path}')
            raise typer.Exit(1)
    return files


def _find_resumable(
    existing_files: list[dict],
    name: str,
    size: int,
    client_last_modified: int | None,
    matched_ids: set[str],
) -> dict | None:
    """Match a local file being (re-)sent to a file already on the draft, by name + size +
    `client_last_modified` -- the CLI's counterpart of `resumable_upload.js`'s `findResumeMatch`.
    `client_last_modified` is compared (when the draft's row has one) so an edited, same-name,
    same-size file doesn't get matched to stale parts from a different version of it, which would
    otherwise silently assemble a corrupted upload out of old and new bytes. `matched_ids` records
    which draft files this send has already matched, so two distinct local files that happen to
    share a name and size (e.g. after `_collect_files` still can't tell them apart) don't both
    match the same draft row.
    """
    for existing in existing_files:
        if existing['id'] in matched_ids:
            continue
        if existing['name'] != name or existing['size'] != size:
            continue
        existing_mtime = existing.get('client_last_modified')
        if existing_mtime is not None and existing_mtime != client_last_modified:
            continue
        matched_ids.add(existing['id'])
        return existing
    return None


def _upload_file(
    headers: dict,
    transfer_id: str,
    name: str,
    path: Path,
    existing_files: list[dict],
    matched_ids: set[str],
) -> None:
    """Upload one file to `transfer_id`'s draft, resuming it (via `.../resume/`) if a matching,
    not-yet-uploaded file is already on the draft -- see `_find_resumable`.

    Missing parts are hashed in a first, streaming pass that keeps only their checksums (never
    their bytes) in memory, then each one is re-read from disk and PUT just before it's needed --
    rather than reading the whole file into memory up front and requesting every part's presigned
    URL in one go, which for a multi-GB file means holding the entire file in RAM, and can mean
    PUTting through a URL that's already expired by the time a later part's turn comes on a slow
    connection (`PART_URL_BATCH_SIZE`).
    """
    size = path.stat().st_size
    client_last_modified = int(path.stat().st_mtime * 1000)
    base = f'{API_BASE_URL}/api/ft/transfers/{transfer_id}/files'
    match = _find_resumable(existing_files, name, size, client_last_modified, matched_ids)

    if match is not None and match['uploaded']:
        logger.info(f'{name}: already uploaded, skipping')
        return

    # part_number -> {'etag': ..., 'checksum_sha256': ...}. S3 requires the checksum for every
    # part once the multipart upload was created with a checksum algorithm (which every upload
    # here is), including parts a resume isn't re-uploading -- see
    # `services.storage.S3Storage.complete_multipart_upload`'s docstring.
    uploaded_parts: dict[int, dict[str, str]] = {}
    if match is not None:
        file_id = match['id']
        response = _request('post', f'{base}/{file_id}/resume/', headers=headers)
        _raise_for_api_error(response)
        resumed = response.json()
        part_size = resumed['part_size_bytes']
        part_count = resumed['part_count']
        uploaded_parts = {
            p['part_number']: {'etag': p['etag'], 'checksum_sha256': p.get('checksum_sha256', '')}
            for p in resumed['uploaded_parts']
        }
        if resumed['restarted']:
            logger.info(f'{name}: previous upload had expired, restarting it')
        elif uploaded_parts:
            logger.info(f'{name}: resuming ({len(uploaded_parts)}/{part_count} parts already up)')
    else:
        response = _request(
            'post',
            f'{base}/',
            json={'name': name, 'size': size, 'client_last_modified': client_last_modified},
            headers=headers,
        )
        _raise_for_api_error(response)
        added = response.json()
        file_id = added['file_id']
        part_size = added['part_size_bytes']
        part_count = added['part_count']

    final_parts: list[dict] = [
        {'part_number': number, 'etag': part['etag'], 'checksum_sha256': part['checksum_sha256']}
        for number, part in uploaded_parts.items()
    ]

    missing = [n for n in range(1, part_count + 1) if n not in uploaded_parts]
    if missing:
        checksums: dict[int, str] = {}
        with path.open('rb') as fh:
            for part_number in missing:
                fh.seek((part_number - 1) * part_size)
                checksums[part_number] = _sha256_base64(fh.read(part_size))

        with path.open('rb') as fh:
            for batch_start in range(0, len(missing), PART_URL_BATCH_SIZE):
                batch = missing[batch_start : batch_start + PART_URL_BATCH_SIZE]
                request_parts = [{'part_number': n, 'checksum_sha256': checksums[n]} for n in batch]
                response = _request(
                    'post',
                    f'{base}/{file_id}/parts/',
                    json={'parts': request_parts},
                    headers=headers,
                )
                _raise_for_api_error(response)
                urls = response.json()['urls']

                for part_number in batch:
                    fh.seek((part_number - 1) * part_size)
                    blob = fh.read(part_size)
                    checksum = checksums[part_number]
                    put_response = _request(
                        'put',
                        urls[str(part_number)],
                        data=blob,
                        headers={'x-amz-checksum-sha256': checksum},
                        timeout=PART_UPLOAD_TIMEOUT_SECONDS,
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
    response = _request(
        'post', f'{base}/{file_id}/complete/', json={'parts': final_parts}, headers=headers
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
    max_downloads: int | None = typer.Option(
        None, '--max-downloads', help='Cap on total downloads (unset = uncapped).'
    ),
    password: str = typer.Option('', '--password', help='Optional download password.'),
    draft_id: str | None = typer.Option(
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
        response = _request('get', f'{API_BASE_URL}/api/ft/transfers/{draft_id}', headers=headers)
        _raise_for_api_error(response)
        transfer = response.json()
        if transfer['status'] != 'draft':
            logger.error(f'{draft_id} is no longer a draft (status: {transfer["status"]}).')
            raise typer.Exit(1)
        transfer_id = transfer['id']
        existing_files = transfer['files']
        logger.info(f'Resuming draft {transfer_id}')
    else:
        response = _request('post', f'{API_BASE_URL}/api/ft/transfers/', json={}, headers=headers)
        _raise_for_api_error(response)
        transfer_id = response.json()['id']
        existing_files = []
        logger.info(f'Draft created: {transfer_id}')

    # From here on, a draft exists: any failure -- a network error, or an API error surfaced as
    # `typer.Exit` by `_raise_for_api_error` (a validation error while uploading, insufficient
    # credits at finalize, ...) -- should point back at it, so re-running doesn't start over.
    try:
        matched_ids: set[str] = set()
        for name, path in files:
            _upload_file(headers, transfer_id, name, path, existing_files, matched_ids)

        response = _request(
            'post',
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
    except typer.Exit:
        logger.error(f'Re-run with --draft-id {transfer_id} to resume.')
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f'{e}')
        logger.error(f'Re-run with --draft-id {transfer_id} to resume.')
        raise typer.Exit(1)

    result = response.json()
    logger.info('Sent!')
    logger.info(f'Link: {result["download_url"]}')


@app.command(name='list')
def filetransfer_list(
    environment: EnvironmentAnnotation,
    filter: str = typer.Option(  # noqa: A002 -- matches the API's own `?filter=` query param
        'active', '--filter', help='"active", "ended" or "all".'
    ),
    limit: int = typer.Option(20, '--limit', help='Max transfers to show.'),
    offset: int = typer.Option(0, '--offset', help='Number of transfers to skip.'),
):
    """
    List your transfers.

    Example:
        filetransfer list
        filetransfer list --filter ended
        filetransfer list --limit 50 --offset 50
    """
    set_environment(environment)
    response = _request(
        'get',
        f'{API_BASE_URL}/api/ft/transfers/',
        params={'filter': filter, 'limit': limit, 'offset': offset},
        headers=get_headers(),
    )
    _raise_for_api_error(response)
    body = response.json()
    transfers = body['results']

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

    shown_through = body['offset'] + len(transfers)
    if shown_through < body['count']:
        logger.info(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]}. '
            f'Use --offset {shown_through} to see more.'
        )


@app.command(name='show')
def filetransfer_show(environment: EnvironmentAnnotation, transfer_id: str):
    """
    Show details of a specific transfer.

    Example:
        filetransfer show 1b6e2f4a-...
    """
    set_environment(environment)

    response = _request(
        'get', f'{API_BASE_URL}/api/ft/transfers/{transfer_id}', headers=get_headers()
    )
    _raise_for_api_error(response)
    transfer = response.json()

    console = Console()
    console.print(f'[cyan]ID:[/cyan] {transfer["id"]}')
    console.print(f'[cyan]Name:[/cyan] {transfer["display_name"]}')
    console.print(f'[cyan]Status:[/cyan] {transfer["status"]}')
    console.print('[cyan]Recipients:[/cyan]')
    if transfer['recipients']:
        for recipient in transfer['recipients']:
            last_sent = recipient['last_sent_at'] or 'never'
            console.print(
                f'  - {recipient["email"]} (id: {recipient["id"]}, last sent: {last_sent})'
            )
    else:
        console.print('  (none)')
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
    response = _request(
        'patch',
        f'{API_BASE_URL}/api/ft/transfers/{transfer_id}',
        json=payload,
        headers=get_headers(),
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
    date: str | None = typer.Option(
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
    max_downloads: int | None = typer.Argument(None, help='Omit for uncapped.'),
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
def filetransfer_set_password(
    environment: EnvironmentAnnotation,
    transfer_id: str,
    password: str | None = typer.Argument(
        None, help='Password. Omit to be prompted, or set FILETRANSFER_PASSWORD.'
    ),
):
    """
    Set (or change) a transfer's download password.

    Example:
        filetransfer set-password 1b6e2f4a-...
        filetransfer set-password 1b6e2f4a-... sekret
    """
    set_environment(environment)
    password = _password_from_prompt_or_env(password)
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
    set_environment(environment)

    response = _request(
        'post',
        f'{API_BASE_URL}/api/ft/transfers/{transfer_id}/recipients',
        json={'recipients': to},
        headers=get_headers(),
    )
    _raise_for_api_error(response)
    emails = ', '.join(r['email'] for r in response.json()['recipients'])
    logger.info(f'{transfer_id} recipients updated: {emails}')


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
    set_environment(environment)

    response = _request(
        'post',
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
    set_environment(environment)

    response = _request(
        'delete', f'{API_BASE_URL}/api/ft/transfers/{transfer_id}', headers=get_headers()
    )
    if response.status_code != 204:
        _raise_for_api_error(response)
    logger.info(f'{transfer_id} deleted.')


if __name__ == '__main__':
    app()
