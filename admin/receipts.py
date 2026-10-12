#!python
"""
Receipts CLI

Command line interface for the receipts JWT API (`/api/receipts/`, issue #98 phase 4), on the model
of `admin/filetransfer.py`: log in, upload receipts and invoices (PDF, JPG, PNG, HEIC, WebP) with
direct-to-S3 uploads, list and inspect them, export a CSV, and see or regenerate the inbox address
receipts can be emailed to.
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

# A separate token file from `admin/qrcode.py` and `admin/filetransfer.py`: each CLI logs in
# independently (possibly as a different account), so sharing one file would mean the last `login`
# of any of them silently signs the others out too.
TOKEN_FILE = Path.home() / '.receipts_token'

# Ordinary API calls; not the PUT of a file -- see `FILE_UPLOAD_TIMEOUT_SECONDS`.
REQUEST_TIMEOUT_SECONDS = 30
# A receipt can be up to `ReceiptsSettings.max_file_bytes` (15 MB by default); a slow connection
# legitimately needs longer than an ordinary API call to finish PUTting one.
FILE_UPLOAD_TIMEOUT_SECONDS = 300
# Bounded retry for a 429 from the API's own rate limits (`RECEIPTS_UPLOAD_USER` and friends): a
# folder of receipts legitimately calls `uploads` often enough to hit one.
MAX_RATE_LIMIT_RETRIES = 5
# Read a file for hashing in chunks rather than all at once.
HASH_CHUNK_BYTES = 1024 * 1024

#: What the API accepts (`ReceiptsSettings.allowed_types`, checked by the server again against the
#: file's contents): file extension -> content type.
CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.heic': 'image/heic',
    '.heif': 'image/heic',
}


def _api_url(path: str) -> str:
    """`<site>/api/receipts<path>`. The site comes from `API_BASE_URL`, else the selected
    environment file's `BASE_URL` (both read now, after `set_environment` has loaded that file --
    not at import time), else the local dev server."""
    base = os.environ.get('API_BASE_URL') or os.environ.get('BASE_URL') or 'http://localhost:8000'
    return f'{base.rstrip("/")}/api{path}'


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


def _password_from_prompt_or_env(password: str | None, env_var: str = 'RECEIPTS_PASSWORD') -> str:
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


def _retry_after_seconds(response) -> float:
    header = response.headers.get('Retry-After')
    try:
        return max(0.0, float(header))
    except TypeError, ValueError:
        return 1.0


def _request(method: str, url: str, **kwargs):
    """`requests.<method>(url, **kwargs)`, with a default timeout (`REQUEST_TIMEOUT_SECONDS`,
    overridable per call) and a bounded retry on 429 that honours the response's `Retry-After`
    header -- every HTTP call this CLI makes goes through here rather than calling `requests`
    directly, so both apply uniformly."""
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


def _error_text(response) -> str:
    """`<status>: <message>`, the message being the API's own `{"detail": ...}` (every error
    response in this API -- `ninja.errors.HttpError`, and the site-wide 429 handler in
    `config/api.py` -- carries one) rather than just the bare status code."""
    try:
        detail = response.json().get('detail', response.text)
    except ValueError:
        detail = response.text
    text = f'{response.status_code}: {detail}'
    if response.status_code == 401:
        text += ' (run `login` again)'
    elif response.status_code == 402:
        text += ' (top up your credits on the website)'
    return text


def _raise_for_api_error(response) -> None:
    """Exit non-zero with the API's own error message unless the response is a success."""
    if response.ok:
        return
    logger.error(_error_text(response))
    raise typer.Exit(1)


def _sha256_base64_file(path: Path) -> str:
    """Base64-encoded SHA-256 digest of the file at `path`, read in chunks. This is the value S3
    checks the upload against (`x-amz-checksum-sha256`) and the API binds into the presigned URL."""
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        while chunk := fh.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode()


def _content_type(path: Path) -> str | None:
    """The content type to claim for `path`, or `None` for a type the API doesn't accept."""
    return CONTENT_TYPES.get(path.suffix.lower())


def _collect_files(paths: list[Path]) -> list[Path]:
    """Expand files and folders (recursively, sorted for deterministic output) into the flat list
    of files to upload.

    A file with an extension the API doesn't accept, or an empty one, is skipped with a warning
    rather than aborting the whole batch -- a folder of scans easily holds a stray `.txt` or
    `.DS_Store`. A path that doesn't exist is an error.
    """
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            candidates = sorted(p for p in path.rglob('*') if p.is_file())
        elif path.is_file():
            candidates = [path]
        else:
            logger.error(f'Not found: {path}')
            raise typer.Exit(1)
        for candidate in candidates:
            if _content_type(candidate) is None:
                logger.warning(f'Skipping unsupported file type: {candidate}')
            elif candidate.stat().st_size == 0:
                logger.warning(f'Skipping empty file: {candidate}')
            else:
                found.append(candidate)
    return found


class UploadFailed(Exception):
    """One file's upload failed; the message says why. The batch carries on with the next file."""


def _upload_receipt(headers: dict, path: Path) -> dict:
    """Upload one file: `POST /uploads` for a presigned URL, `PUT` the bytes to it with the
    headers the API returned, then `POST /uploads/{id}/complete`. Returns the receipt.

    Raises:
        UploadFailed: the API refused the file (with its message), or the `PUT` failed.
    """
    import requests

    content_type = _content_type(path)
    assert content_type is not None  # `_collect_files` only lets supported files through
    size = path.stat().st_size
    start = _request(
        'post',
        _api_url('/receipts/uploads'),
        json={
            'filename': path.name,
            'size': size,
            'content_type': content_type,
            'checksum_sha256': _sha256_base64_file(path),
        },
        headers=headers,
    )
    if not start.ok:
        raise UploadFailed(_error_text(start))
    ticket = start.json()

    try:
        put = _request(
            'put',
            ticket['url'],
            data=path.read_bytes(),
            headers=ticket['headers'],
            timeout=FILE_UPLOAD_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as e:
        raise UploadFailed(f'upload failed: {e}') from e
    if not put.ok:
        raise UploadFailed(f'the upload to storage failed ({put.status_code})')

    complete = _request(
        'post', _api_url(f'/receipts/uploads/{ticket["receipt_id"]}/complete'), headers=headers
    )
    if not complete.ok:
        raise UploadFailed(_error_text(complete))
    return complete.json()


def _amount(receipt: dict) -> str:
    if receipt['amount'] is None:
        return ''
    return f'{receipt["amount"]} {receipt["currency"]}'.strip()


@app.command(name='login')
def receipts_login(
    environment: EnvironmentAnnotation,
    email: str,
    password: str | None = typer.Argument(
        None, help='Password. Omit to be prompted, or set RECEIPTS_PASSWORD.'
    ),
):
    """
    Authenticate with the API and store the token.

    Example:
        receipts login local me@example.com
        receipts login prod me@example.com mypassword
    """
    import requests

    set_environment(environment)
    password = _password_from_prompt_or_env(password)

    try:
        response = _request(
            'post', _api_url('/auth/login'), json={'email': email, 'password': password}
        )
        response.raise_for_status()
        token = response.json()['access_token']
        save_token(token)
        logger.info('Successfully authenticated!')
    except requests.exceptions.RequestException as e:
        logger.error(f'Login failed: {e}')
        raise typer.Exit(1)


@app.command(name='list')
def receipts_list(
    environment: EnvironmentAnnotation,
    status: str | None = typer.Option(
        None,
        '--status',
        help='needs_review, processed, failed, received, processing, waiting_for_credits, '
        'or in_progress (comma-separated for several).',
    ),
    category: str | None = typer.Option(
        None, '--category', help='A category id, or "none" for uncategorized.'
    ),
    q: str = typer.Option('', '--q', help='Search the vendor and the notes.'),
    date_from: str | None = typer.Option(
        None, '--from', help='Earliest receipt date, YYYY-MM-DD (inclusive).'
    ),
    date_to: str | None = typer.Option(
        None, '--to', help='Latest receipt date, YYYY-MM-DD (inclusive).'
    ),
    sort: str = typer.Option(
        '-receipt_date',
        '--sort',
        help='receipt_date, -receipt_date, created_at or -created_at.',
    ),
    duplicates: bool = typer.Option(
        False, '--duplicates', help='Only receipts flagged as possible duplicates.'
    ),
    trashed: bool = typer.Option(False, '--trashed', help='List the Trash instead.'),
    limit: int = typer.Option(20, '--limit', help='Max receipts to show (1-100).'),
    offset: int = typer.Option(0, '--offset', help='Number of receipts to skip.'),
):
    """
    List your receipts.

    Example:
        receipts list local
        receipts list local --status needs_review
        receipts list local --from 2026-10-01 --to 2026-10-31 --q staples --limit 50
    """
    set_environment(environment)
    params: dict = {'sort': sort, 'limit': limit, 'offset': offset}
    for name, value in (
        ('status', status),
        ('category', category),
        ('q', q),
        ('date_from', date_from),
        ('date_to', date_to),
    ):
        if value:
            params[name] = value
    if duplicates:
        params['duplicates'] = 'true'
    if trashed:
        params['trashed'] = 'true'

    response = _request('get', _api_url('/receipts/receipts'), params=params, headers=get_headers())
    _raise_for_api_error(response)
    body = response.json()
    receipts = body['results']

    if not receipts:
        logger.info('No receipts found.')
        return

    table = Table(title='Your Receipts')
    table.add_column('ID', style='cyan', no_wrap=True)
    table.add_column('Date', style='white', no_wrap=True)
    table.add_column('Vendor', style='white')
    table.add_column('Amount', style='magenta', justify='right')
    table.add_column('Category', style='blue')
    table.add_column('Status', style='green', no_wrap=True)
    for receipt in receipts:
        category_name = receipt['category']['name'] if receipt['category'] else ''
        table.add_row(
            receipt['id'],
            receipt['receipt_date'] or '',
            receipt['vendor'] or receipt['original_filename'],
            _amount(receipt),
            category_name,
            receipt['status'],
        )
    Console().print(table)

    shown_through = body['offset'] + len(receipts)
    if shown_through < body['count']:
        logger.info(
            f'Showing {body["offset"] + 1}-{shown_through} of {body["count"]}. '
            f'Use --offset {shown_through} to see more.'
        )


@app.command(name='upload')
def receipts_upload(
    environment: EnvironmentAnnotation,
    paths: list[Path] = typer.Argument(
        ..., help='Receipt files (PDF, JPG, PNG, HEIC, WebP) or folders of them.'
    ),
):
    """
    Upload receipts and invoices: each file goes straight to storage, then is queued for reading.
    Files of another type are skipped with a warning; a file that fails doesn't stop the rest
    (the exit code is non-zero if any did).

    Example:
        receipts upload local invoice.pdf photo.jpg
        receipts upload prod ./october-scans
    """
    import requests

    set_environment(environment)
    headers = get_headers()

    files = _collect_files(list(paths))
    if not files:
        logger.error('No files to upload.')
        raise typer.Exit(1)

    failed = 0
    for path in files:
        try:
            receipt = _upload_receipt(headers, path)
        except UploadFailed as e:
            failed += 1
            logger.error(f'{path.name}: {e}')
        except requests.exceptions.RequestException as e:
            failed += 1
            logger.error(f'{path.name}: {e}')
        else:
            logger.info(f'{path.name}: uploaded as {receipt["id"]} ({receipt["status"]})')

    logger.info(f'{len(files) - failed} of {len(files)} uploaded.')
    if failed:
        raise typer.Exit(1)


@app.command(name='get')
def receipts_get(environment: EnvironmentAnnotation, receipt_id: str):
    """
    Show everything about one receipt.

    Example:
        receipts get local 1b6e2f4a-...
    """
    set_environment(environment)

    response = _request('get', _api_url(f'/receipts/receipts/{receipt_id}'), headers=get_headers())
    _raise_for_api_error(response)
    receipt = response.json()

    console = Console()
    category = receipt['category']['name'] if receipt['category'] else ''
    rows = [
        ('ID', receipt['id']),
        ('Status', receipt['status']),
        ('Failure', receipt['failure_reason']),
        ('Vendor', receipt['vendor']),
        ('Amount', _amount(receipt)),
        ('Receipt date', receipt['receipt_date'] or ''),
        ('Type', receipt['document_type']),
        ('Number', receipt['document_number']),
        ('Payment method', receipt['payment_method']),
        ('Category', category),
        ('Notes', receipt['notes']),
        ('Missing', ', '.join(receipt['missing_fields'])),
        ('Possible duplicate of', receipt['possible_duplicate_of'] or ''),
        ('Source', receipt['source']),
        ('File', f'{receipt["original_filename"]} ({receipt["size_bytes"]} bytes)'),
        ('Added', receipt['created_at']),
        ('Updated', receipt['updated_at']),
        ('Processed', receipt['processed_at'] or ''),
        ('In the trash since', receipt['deleted_at'] or ''),
    ]
    for label, value in rows:
        if value:
            console.print(f'[cyan]{label}:[/cyan] {value}', highlight=False)


@app.command(name='export')
def receipts_export(
    environment: EnvironmentAnnotation,
    out: Path = typer.Option(..., '--out', '-o', help='Where to write the CSV.'),
    status: str | None = typer.Option(None, '--status', help='As for `list`.'),
    category: str | None = typer.Option(None, '--category', help='As for `list`.'),
    q: str = typer.Option('', '--q', help='Search the vendor and the notes.'),
    date_from: str | None = typer.Option(None, '--from', help='Earliest receipt date.'),
    date_to: str | None = typer.Option(None, '--to', help='Latest receipt date.'),
    sort: str = typer.Option('-receipt_date', '--sort', help='As for `list`.'),
    duplicates: bool = typer.Option(False, '--duplicates', help='Only possible duplicates.'),
):
    """
    Export the matching receipts as a CSV file (the Trash is never exported).

    Example:
        receipts export local --out receipts.csv
        receipts export local --out q3.csv --from 2026-07-01 --to 2026-09-30 --status processed
    """
    set_environment(environment)
    params: dict = {'sort': sort}
    for name, value in (
        ('status', status),
        ('category', category),
        ('q', q),
        ('date_from', date_from),
        ('date_to', date_to),
    ):
        if value:
            params[name] = value
    if duplicates:
        params['duplicates'] = 'true'

    response = _request(
        'get', _api_url('/receipts/export.csv'), params=params, headers=get_headers(), stream=True
    )
    _raise_for_api_error(response)
    size = 0
    with out.open('wb') as fh:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            fh.write(chunk)
            size += len(chunk)
    logger.info(f'Wrote {out} ({size} bytes).')


@app.command(name='inbox')
def receipts_inbox(environment: EnvironmentAnnotation):
    """
    Show the address receipts can be emailed to, who may send to it and the default currency.

    Example:
        receipts inbox local
    """
    set_environment(environment)

    response = _request('get', _api_url('/receipts/inbox'), headers=get_headers())
    _raise_for_api_error(response)
    inbox = response.json()

    console = Console()
    console.print(f'[cyan]Address:[/cyan] {inbox["address"]}', highlight=False)
    allowlist = inbox['sender_allowlist']
    console.print(
        f'[cyan]Accepted senders:[/cyan] {", ".join(allowlist) if allowlist else "anyone"}',
        highlight=False,
    )
    console.print(f'[cyan]Default currency:[/cyan] {inbox["default_currency"]}')
    confirmation = inbox['forwarding_confirmation']
    if confirmation:
        console.print(
            f'[cyan]Forwarding confirmation (received {confirmation["received_at"]}):[/cyan] '
            f'code {confirmation["code"]}',
            highlight=False,
        )
        if confirmation['url']:
            console.print(f'  {confirmation["url"]}', highlight=False)


@app.command(name='regenerate')
def receipts_regenerate(
    environment: EnvironmentAnnotation,
    yes: bool = typer.Option(False, '--yes', '-y', help='Do not ask for confirmation.'),
):
    """
    Retire the inbox address and issue a new one. Mail still sent to the old address is dropped.

    Example:
        receipts regenerate local
        receipts regenerate local --yes
    """
    set_environment(environment)
    headers = get_headers()
    if not yes and not typer.confirm(
        'The current address stops working immediately. Generate a new one?'
    ):
        logger.info('Cancelled.')
        raise typer.Exit(1)

    response = _request('post', _api_url('/receipts/inbox/regenerate'), headers=headers)
    _raise_for_api_error(response)
    logger.info(f'New address: {response.json()["address"]}')


if __name__ == '__main__':
    app()
